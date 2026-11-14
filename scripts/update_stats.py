"""Refresh the stats shown on the iaac-maai GitHub organization page.

Writes:
  stats/hf.json            totals for the iaac-maai Hugging Face org (read by shields.io badges)
  stats/activity.svg       cumulative commits over time, whole organization
  stats/top-YYYY-YY.svg    top contributors per academic year (Sep-Aug)
  profile/README.md        the per-year <details> sections between the years markers

Standard library only. Set GITHUB_TOKEN to avoid API rate limits.
"""

from __future__ import annotations

import json
import math
import os
import re
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from html import escape
from pathlib import Path

HF_ORG = "iaac-maai"
GH_ORG = "iaac-maai"
TOP_N = 5  # lines on the chart (categorical slots 1-5)
ROOT = Path(__file__).resolve().parent.parent
STATS = ROOT / "stats"

# AI agents and automation accounts never count as contributors.
# Only the commit author is counted, so Co-Authored-By trailers never add
# anyone; this list covers agents that author commits themselves.
EXCLUDED_LOGINS = {
    "claude", "anthropic-claude", "claude-code", "copilot", "copilot-swe-agent",
    "devin-ai-integration", "cursor-agent", "cursoragent", "github-actions",
    "dependabot", "renovate", "openai-codex", "codex", "chatgpt-codex-connector",
    "sweep-ai", "aider", "gemini-code-assist", "google-labs-jules",
}
EXCLUDED_PATTERN = re.compile(r"(\[bot\]|bot|agent|ai-assistant)$", re.IGNORECASE)

# Categorical slots 1-5 of the dataviz reference palette (fixed order, light / dark).
SERIES_LIGHT = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4"]
SERIES_DARK = ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181"]


def get_json(url: str, token: str | None = None):
    headers = {"User-Agent": "iaac-maai-org-stats", "Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=60) as r:
        body = r.read()
        return json.loads(body) if body else []


# --------------------------------------------------------------------------- HF
def hf_stats() -> dict:
    totals = {"downloads": 0, "likes": 0}
    counts = {}
    for kind in ("models", "datasets", "spaces"):
        # Spaces have no download counter; the API rejects that expand for them.
        expand = "&expand[]=likes" if kind == "spaces" else "&expand[]=downloadsAllTime&expand[]=likes"
        url = f"https://huggingface.co/api/{kind}?author={HF_ORG}&limit=1000{expand}"
        items = get_json(url)
        counts[kind] = len(items)
        for it in items:
            totals["downloads"] += int(it.get("downloadsAllTime") or 0)
            totals["likes"] += int(it.get("likes") or 0)
    return {**totals, **counts,
            "updated": datetime.now(timezone.utc).strftime("%Y-%m-%d")}


# ----------------------------------------------------------------------- GitHub
def is_human(user: dict | None) -> bool:
    if not user:
        return False
    login = (user.get("login") or "").lower()
    if not login or user.get("type") != "User":
        return False
    return login not in EXCLUDED_LOGINS and not EXCLUDED_PATTERN.search(login)


def gh_commit_dates(token: str | None) -> dict[str, list[date]]:
    """Commit dates per human author across public org repos (default branches)."""
    repos, page = [], 1
    while True:
        batch = get_json(f"https://api.github.com/orgs/{GH_ORG}/repos"
                         f"?type=public&per_page=100&page={page}", token)
        repos += batch
        if len(batch) < 100:
            break
        page += 1

    dates: dict[str, list[date]] = {}
    for repo in repos:
        if repo.get("private") or repo.get("fork") or repo.get("archived"):
            continue
        if repo["name"] == ".github":  # the stats bot commits here
            continue
        # Only count commits made after the repo was created in the org, so
        # history imported from upstream projects (e.g. robosuite) is ignored.
        since = repo["created_at"]
        page = 1
        while True:
            try:
                batch = get_json(f"https://api.github.com/repos/{GH_ORG}/{repo['name']}"
                                 f"/commits?since={since}&per_page=100&page={page}", token)
            except urllib.error.HTTPError as e:
                if e.code == 409:  # empty repository
                    break
                raise
            for c in batch:
                if not is_human(c.get("author")):
                    continue
                day = datetime.fromisoformat(
                    c["commit"]["author"]["date"].replace("Z", "+00:00")).date()
                dates.setdefault(c["author"]["login"], []).append(day)
            if len(batch) < 100:
                break
            page += 1
    return dates


# -------------------------------------------------------------------------- SVG
def nice_step(peak: int, ticks: int = 4) -> int:
    raw = max(peak, 1) / ticks
    mag = 10 ** math.floor(math.log10(raw))
    for m in (1, 2, 2.5, 5, 10):
        if raw <= m * mag:
            return max(1, int(m * mag))
    return int(10 * mag)


def cumulative_points(days: list[date], start: date, today: date, x, y) -> tuple[str, int]:
    """Step-after cumulative polyline from start to today."""
    per_day: dict[date, int] = {}
    for d in days:
        per_day[d] = per_day.get(d, 0) + 1
    pts, total = [(x(start), y(0))], 0
    for d in sorted(per_day):
        pts.append((x(d), y(total)))
        total += per_day[d]
        pts.append((x(d), y(total)))
    pts.append((x(today), y(total)))
    return " ".join(f"{px:.1f},{py:.1f}" for px, py in pts), total


def time_ticks(start: date, today: date, max_ticks: int = 8) -> list[date]:
    """First-of-month ticks, thinned to every 1/2/3/6/12 months so labels never collide."""
    months = []
    m = date(start.year, start.month, 1)
    while m <= today:
        if m >= start:
            months.append(m)
        m = date(m.year + (m.month == 12), m.month % 12 + 1, 1)
    every = next((k for k in (1, 2, 3, 6, 12) if math.ceil(len(months) / k) <= max_ticks), 12)
    aligned = [d for d in months if (d.month - 1) % every == 0]
    return aligned or months[:1]


W, L, R = 760, 56, 176          # width and plot margins (right side holds end-of-line labels)
PW = W - L - R
FONT = "-apple-system,Segoe UI,Helvetica,Arial,sans-serif"
RAW = "https://raw.githubusercontent.com/iaac-maai/.github/main"
README = ROOT / "profile" / "README.md"
YEARS_START, YEARS_END = "<!-- years:start -->", "<!-- years:end -->"


def svg_open(h: int, label: str) -> list[str]:
    css = [
        ".bg{fill:#ffffff} .t1{fill:#1f2328} .t2{fill:#59636e} .grid{stroke:#eaeef2} "
        ".axis{stroke:#d1d9e0} .ring{stroke:#ffffff} .total{stroke:#1f2328} .totalf{fill:#1f2328} .area{fill:#1f2328;opacity:.06}",
        *[f".s{i}{{stroke:{c}}} .f{i}{{fill:{c}}}" for i, c in enumerate(SERIES_LIGHT)],
        "@media (prefers-color-scheme: dark){",
        "  .bg{fill:#0d1117} .t1{fill:#e6edf3} .t2{fill:#9198a1} .grid{stroke:#1f252c} "
        ".axis{stroke:#30363d} .ring{stroke:#0d1117} .total{stroke:#e6edf3} .totalf{fill:#e6edf3} .area{fill:#e6edf3;opacity:.08}",
        *[f"  .s{i}{{stroke:{c}}} .f{i}{{fill:{c}}}" for i, c in enumerate(SERIES_DARK)],
        "}",
        f"text{{font-family:{FONT}}} .small{{font-size:12px}} .val{{font-size:12px;font-weight:600}}",
        ".line{fill:none;stroke-width:2;stroke-linejoin:round;stroke-linecap:round}",
    ]
    return [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{h}" viewBox="0 0 {W} {h}" '
            f'role="img" aria-label="{escape(label)}">',
            "<style>", *css, "</style>",
            f'<rect class="bg" width="{W}" height="{h}" rx="10"/>']


def y_scale(top: int, h: int, peak: int):
    step = nice_step(peak, 3 if h < 150 else 4)
    ymax = math.ceil(max(peak, 1) / step) * step
    return (lambda v: top + h - h * v / ymax), step, ymax


def x_scale(start: date, end: date):
    span = max((end - start).days, 1)
    return lambda d: L + PW * (d - start).days / span


def draw_grid(out: list[str], y, step: int, ymax: int) -> None:
    for v in range(0, ymax + 1, step):
        yy = y(v)
        out.append(f'<line class="{"axis" if v == 0 else "grid"}" x1="{L}" x2="{L + PW}" '
                   f'y1="{yy:.1f}" y2="{yy:.1f}" stroke-width="1"/>')
        out.append(f'<text class="small t2" x="{L - 8}" y="{yy + 4:.1f}" text-anchor="end">{v:,}</text>')


def draw_x_axis(out: list[str], base: float, x, ticks: list[date], fmt: str) -> None:
    for d in ticks:
        out.append(f'<text class="small t2" x="{x(d):.1f}" y="{base + 18:.1f}" '
                   f'text-anchor="middle">{d.strftime(fmt)}</text>')


def render_org_svg(dates: dict[str, list[date]], today: date) -> str:
    """Whole organization: cumulative commits since the first commit."""
    top, h = 44, 130
    H = top + h + 48
    all_days = [d for v in dates.values() for d in v]
    out = svg_open(H, f"Cumulative commits over time across the {GH_ORG} organization")
    out.append(f'<text class="small t2" x="20" y="24">Cumulative commits across public {GH_ORG} '
               "repositories · AI agents, bots and imported upstream history excluded</text>")
    if all_days:
        start = min(all_days)
        x = x_scale(start, today)
        y, step, ymax = y_scale(top, h, len(all_days))
        draw_grid(out, y, step, ymax)
        pts, total = cumulative_points(all_days, start, today, x, y)
        out.append(f'<polygon class="area" points="{x(start):.1f},{y(0):.1f} {pts} {x(today):.1f},{y(0):.1f}"/>')
        out.append(f'<polyline class="line total" points="{pts}"/>')
        out.append(f'<circle class="totalf ring" cx="{x(today):.1f}" cy="{y(total):.1f}" r="4" stroke-width="2"/>')
        out.append(f'<text class="val t1" x="{x(today) + 10:.1f}" y="{y(total) + 4:.1f}">{total:,}</text>')
        out.append(f'<text class="small t2" x="{x(today) + 16 + 8 * len(f"{total:,}"):.1f}" '
                   f'y="{y(total) + 4:.1f}">commits · {len(dates)} people</text>')
        draw_x_axis(out, top + h, x, time_ticks(start, today), "%b %y")
    out.append(f'<text class="small t2" x="{W - 20}" y="{H - 12}" text-anchor="end">'
               f'Updated {today.strftime("%-d %b %Y")}</text>')
    out.append("</svg>")
    return "\n".join(out) + "\n"


def academic_year(d: date) -> int:
    """Start year of the Sep-Aug academic year containing d."""
    return d.year if d.month >= 9 else d.year - 1


def ay_label(y: int) -> str:
    return f"{y}/{str(y + 1)[-2:]}"


def render_year_svg(dates: dict[str, list[date]], ay: int, today: date) -> tuple[str, list]:
    """Top contributors within one academic year (1 Sep ay - 31 Aug ay+1)."""
    start, end = date(ay, 9, 1), date(ay + 1, 8, 31)
    last = min(end, today)                   # the current year's lines stop today
    window = {k: [d for d in v if start <= d <= end] for k, v in dates.items()}
    window = {k: v for k, v in window.items() if v}
    ranked = sorted(window, key=lambda k: (-len(window[k]), k.lower()))[:TOP_N]

    # Legend (always present for >= 2 series), wrapping onto extra rows if needed
    legend, lx, row = [], 20, 0
    for i, login in enumerate(ranked):
        w = 20 + 7.4 * len(login) + 22
        if lx + w > W - 20 and lx > 20:
            lx, row = 20, row + 1
        legend.append((i, login, lx, 24 + 20 * row))
        lx += w
    top, h = 50 + 20 * row, 200
    H = top + h + 48
    out = svg_open(H, f"Cumulative commits of the top {len(ranked)} contributors to {GH_ORG} "
                      f"in academic year {ay_label(ay)}")
    for i, login, lx, ty in legend:
        out.append(f'<rect class="f{i}" x="{lx}" y="{ty - 6}" width="14" height="4" rx="2"/>')
        out.append(f'<text class="small t1" x="{lx + 20}" y="{ty}">{escape(login)}</text>')

    x = x_scale(start, end)
    y, step, ymax = y_scale(top, h, max(len(window[k]) for k in ranked))
    draw_grid(out, y, step, ymax)
    ends = []
    for i, login in enumerate(ranked):
        pts, total = cumulative_points(window[login], start, last, x, y)
        out.append(f'<polyline class="line s{i}" points="{pts}"/>')
        ends.append((y(total), i, login, total))
    for ey, i, _, _ in ends:
        out.append(f'<circle class="f{i} ring" cx="{x(last):.1f}" cy="{ey:.1f}" r="4" stroke-width="2"/>')
    ends.sort()                              # top first; nudge labels apart
    label_y: list[float] = []
    for ey, *_ in ends:
        label_y.append(max(ey, label_y[-1] + 16) if label_y else ey)
    shift = max(0.0, label_y[-1] - (top + h))
    for (_, _, login, total), ly in zip(ends, label_y):
        ly -= shift
        out.append(f'<text class="val t1" x="{x(last) + 10:.1f}" y="{ly + 4:.1f}">{total:,}</text>')
        out.append(f'<text class="small t2" x="{x(last) + 16 + 8 * len(f"{total:,}"):.1f}" '
                   f'y="{ly + 4:.1f}">{escape(login)}</text>')
    ticks = [date(ay + (m < 9), m, 1) for m in (9, 11, 1, 3, 5, 7)]
    draw_x_axis(out, top + h, x, ticks, "%b %y")
    out.append("</svg>")
    return "\n".join(out) + "\n", [(k, len(window[k])) for k in ranked]


def year_sections(years: list[int], today: date) -> str:
    """Collapsible <details> per academic year, newest first and open."""
    blocks = []
    for n, ay in enumerate(sorted(years, reverse=True)):
        slug = ay_label(ay).replace("/", "-")
        note = " · in progress" if ay == academic_year(today) else ""
        blocks.append(
            f'<details{" open" if n == 0 else ""}>\n'
            f"<summary><b>{ay_label(ay)}</b>{note}</summary>\n<br/>\n"
            f'<img src="{RAW}/stats/top-{slug}.svg" alt="Top contributors in {ay_label(ay)}" width="760"/>\n'
            "</details>")
    return "\n\n".join(blocks)


def update_readme(sections: str) -> None:
    text = README.read_text()
    a, b = text.index(YEARS_START) + len(YEARS_START), text.index(YEARS_END)
    README.write_text(text[:a] + "\n" + sections + "\n" + text[b:])


def main() -> None:
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    STATS.mkdir(exist_ok=True)
    today = datetime.now(timezone.utc).date()

    hf = hf_stats()
    (STATS / "hf.json").write_text(json.dumps(hf, indent=2) + "\n")
    print("HF:", hf)

    dates = gh_commit_dates(token)
    (STATS / "activity.svg").write_text(render_org_svg(dates, today))

    years = sorted({academic_year(d) for v in dates.values() for d in v})
    for old in STATS.glob("top-*.svg"):
        old.unlink()
    for ay in years:
        svg, top = render_year_svg(dates, ay, today)
        (STATS / f"top-{ay_label(ay).replace('/', '-')}.svg").write_text(svg)
        print(ay_label(ay), top)
    update_readme(year_sections(years, today))


if __name__ == "__main__":
    main()
