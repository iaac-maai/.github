"""Refresh the stats shown on the iaac-maai GitHub organization page.

Writes:
  stats/hf.json            totals for the iaac-maai Hugging Face org (read by shields.io badges)
  stats/activity.svg       cumulative commits over time, whole organization
  stats/top-YYYY-YY.svg    how the top 5 changed in each academic year (Sep-Aug)
  stats/*-static.svg       still copies of the charts, for visitors who prefer reduced motion
  profile/README.md        the per-year <details> sections between the years markers

Standard library only. Set GITHUB_TOKEN to avoid API rate limits.
"""

from __future__ import annotations

import json
import math
import os
import urllib.error
import urllib.request
from bisect import bisect_right
from datetime import date, datetime, timedelta, timezone
from html import escape
from pathlib import Path

HF_ORG = "iaac-maai"
GH_ORG = "iaac-maai"
TOP_N = 5  # size of the ranking shown on the yearly charts
ROOT = Path(__file__).resolve().parent.parent
STATS = ROOT / "stats"

# AI agents and automation accounts never count as contributors.
# Only the commit author is counted, so Co-Authored-By trailers never add
# anyone; this list covers agents that author commits themselves. Accounts are
# matched by exact name: students can have usernames ending in "-bot" or "-agent".
EXCLUDED_LOGINS = {
    "claude", "anthropic-claude", "claude-code", "copilot", "copilot-swe-agent",
    "devin-ai-integration", "cursor-agent", "cursoragent", "github-actions",
    "dependabot", "renovate", "renovate-bot", "openai-codex", "codex", "chatgpt-codex-connector",
    "sweep-ai", "aider", "gemini-code-assist", "google-labs-jules",
}

# Categorical slots 1-8 of the dataviz reference palette (fixed order, light / dark).
SERIES_LIGHT = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
SERIES_DARK = ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300", "#9085e9", "#e66767"]
OTHER_LIGHT, OTHER_DARK = "#8c959f", "#6e7681"   # people beyond the 8 hues


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
    return login not in EXCLUDED_LOGINS and not login.endswith("[bot]")


def gh_commit_dates(token: str | None) -> dict[str, list[date]]:
    """Commit dates per human author across public org repos (default branches).

    Archived repos count; forks don't. A commit that is in several repos counts once.
    """
    repos, page = [], 1
    while True:
        batch = get_json(f"https://api.github.com/orgs/{GH_ORG}/repos"
                         f"?type=public&per_page=100&page={page}", token)
        repos += batch
        if len(batch) < 100:
            break
        page += 1

    dates: dict[str, list[date]] = {}
    seen: set[str] = set()
    for repo in repos:
        if repo.get("private") or repo.get("fork"):
            continue
        if repo["name"] == ".github":  # the stats bot commits here
            continue
        page = 1
        while True:
            try:
                batch = get_json(f"https://api.github.com/repos/{GH_ORG}/{repo['name']}"
                                 f"/commits?per_page=100&page={page}", token)
            except urllib.error.HTTPError as e:
                if e.code == 409:  # empty repository
                    break
                raise
            for c in batch:
                if c["sha"] in seen or not is_human(c.get("author")):
                    continue
                seen.add(c["sha"])
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
        *[f".s{i}{{stroke:{c}}} .f{i}{{fill:{c}}}" for i, c in enumerate([*SERIES_LIGHT, OTHER_LIGHT])],
        "@media (prefers-color-scheme: dark){",
        "  .bg{fill:#0d1117} .t1{fill:#e6edf3} .t2{fill:#9198a1} .grid{stroke:#1f252c} "
        ".axis{stroke:#30363d} .ring{stroke:#0d1117} .total{stroke:#e6edf3} .totalf{fill:#e6edf3} .area{fill:#e6edf3;opacity:.08}",
        *[f"  .s{i}{{stroke:{c}}} .f{i}{{fill:{c}}}" for i, c in enumerate([*SERIES_DARK, OTHER_DARK])],
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


# -------------------------------------------------------------------- animation
# GitHub shows the charts through <img>: no scripts, but CSS animations run.
# Every element is drawn in its final state and the keyframes replay how it got
# there, so without animation (or with reduced motion) the chart is complete.
PLAY, HOLD = 12.0, 6.0         # seconds of replay, then seconds resting on the final state
ROW = 18                       # line height of the counter strips


def playback(days: list[date], start: date, end: date, extra: list[date] = []) -> tuple[list[date], list[float]]:
    """Key dates and the moment (0-1) each one is reached during the replay.

    Busy days get more screen time than quiet months. Only the playback speed
    varies: the date axis itself stays linear.
    """
    per_day: dict[date, int] = {}
    for d in days:
        per_day[d] = per_day.get(d, 0) + 1
    keys = {start, end, *extra}
    for d in per_day:
        keys |= {d, max(start, d - timedelta(days=1))}
    ks = sorted(keys)
    span = max((end - start).days, 1)
    busy = sum(math.sqrt(n) for n in per_day.values()) or 1
    times = [0.0]
    for a, b in zip(ks, ks[1:]):
        times.append(times[-1] + 0.2 * (b - a).days / span + 0.8 * math.sqrt(per_day.get(b, 0)) / busy)
    total = times[-1] or 1
    return ks, [t / total for t in times]


def pct(t: float) -> str:
    return f"{100 * t * PLAY / (PLAY + HOLD):.3f}%"


class Motion:
    """Collects keyframes; everything loops on the same clock."""

    def __init__(self) -> None:
        self.css: list[str] = []
        self.n = 0

    def add(self, frames: list[tuple[float, str]], timing: str = "linear") -> str:
        """frames: (moment 0-1, declaration). Returns the class to put on the element."""
        if frames[0][0] > 0:        # hold the first state until its moment comes
            frames = [(0.0, frames[0][1]), *frames]
        # A frame in the middle of a run of identical values adds nothing.
        kept = [f for i, f in enumerate(frames)
                if i in (0, len(frames) - 1) or not frames[i - 1][1] == f[1] == frames[i + 1][1]]
        body = "".join(f"{pct(t)}{{{d}}}" for t, d in kept)
        self.n += 1
        self.css.append(f"@keyframes k{self.n}{{{body}to{{{frames[-1][1]}}}}}"
                        f".a{self.n}{{animation:k{self.n} {PLAY + HOLD:g}s {timing} infinite}}")
        return f"a{self.n}"

    def style(self) -> str:
        fade = ("@keyframes fade{0%{opacity:0}1.5%{opacity:1}97.5%{opacity:1}to{opacity:0}}"
                f".fade{{animation:fade {PLAY + HOLD:g}s linear infinite}}")
        return ("<style>@media (prefers-reduced-motion:no-preference){\n"
                + "\n".join([fade, *self.css]) + "\n}</style>")


def draw_line(out: list[str], mo: Motion, cls: str, pts: list[tuple[float, float]], times: list[float]) -> None:
    """A line that draws itself, its tip following the same path as the dot."""
    pts = [(round(px, 1), round(py, 1)) for px, py in pts]
    run = [0.0]
    for a, b in zip(pts, pts[1:]):
        run.append(run[-1] + math.dist(a, b))
    full = run[-1] + 1
    a = mo.add([(t, f"stroke-dashoffset:{full - r:.1f}px") for t, r in zip(times, run)])
    path = " ".join(f"{px},{py}" for px, py in pts)
    out.append(f'<polyline class="line {cls} {a}" points="{path}" stroke-dasharray="{full:.1f}"/>')


def moving(mo: Motion, pts: list[tuple[float, float]], times: list[float]) -> str:
    """Attributes for a group that travels along pts and rests on the last one."""
    a = mo.add([(t, f"transform:translate({px:.1f}px,{py:.1f}px)") for t, (px, py) in zip(times, pts)])
    return f'class="{a}" transform="translate({pts[-1][0]:.1f} {pts[-1][1]:.1f})"'


def ticking(rows: list[tuple[int, ...]], times: list[float]) -> tuple[list[tuple[int, ...]], list[float]]:
    """Fill in the numbers between key dates so counters keep up with their dots (about 10 per second)."""
    vals, ts = [rows[0]], [times[0]]
    for a, b, t0, t1 in zip(rows, rows[1:], times, times[1:]):
        n = max(1, min(int((t1 - t0) * PLAY * 10), max(abs(q - p) for p, q in zip(a, b))))
        for j in range(1, n + 1):
            vals.append(tuple(p + (q - p) * j // n for p, q in zip(a, b)))
            ts.append(t0 + (t1 - t0) * j / n)
    return vals, ts


def draw_counter(out: list[str], mo: Motion, texts: list[str], times: list[float], x: int, anchor: str) -> None:
    """Text that changes over time: a strip of values moved behind a one-line window."""
    values = [t for i, t in enumerate(texts) if i == 0 or t != texts[i - 1]]
    frames, row = [], -1
    for i, (t, txt) in enumerate(zip(times, texts)):
        row += i == 0 or txt != texts[i - 1]
        frames.append((t, f"transform:translateY({-row * ROW}px)"))
    a = mo.add(frames, "step-end")
    out.append(f'<g clip-path="url(#win)"><g class="{a}" transform="translate(0 {-(len(values) - 1) * ROW})">')
    for k, txt in enumerate(values):
        out.append(f'<text x="{x}" y="{4 + k * ROW}" text-anchor="{anchor}">{txt}</text>')
    out.append("</g></g>")


WINDOW = '<defs><clipPath id="win"><rect x="0" y="-9" width="220" height="18"/></clipPath></defs>'


def spread(ys: list[float], top: float, bottom: float, gap: float = 16) -> list[float]:
    """Nudge label positions (given in ranking order) apart, keeping them inside the panel."""
    out: list[float] = []
    for v in ys:
        out.append(max(v, out[-1] + gap) if out else max(v, top))
    limit = bottom
    for i in range(len(out) - 1, -1, -1):
        out[i] = min(out[i], limit)
        limit = out[i] - gap
    return out


def counts_at(days: list[date], keys: list[date]) -> list[int]:
    ordered = sorted(days)
    return [bisect_right(ordered, k) for k in keys]


def render_org_svg(dates: dict[str, list[date]], today: date) -> str:
    """Whole organization: commits accumulated within each academic year (resets every 1 Sep)."""
    top, h = 64, 130
    H = top + h + 48
    all_days = [d for v in dates.values() for d in v]
    out = svg_open(H, f"Commits per academic year across the {GH_ORG} organization, "
                      "accumulating from zero every September")
    out.append(f'<text class="small t2" x="20" y="24">Commits across public {GH_ORG} repositories, '
               "accumulated per academic year (resets every 1 September)</text>")
    out.append('<text class="small t2" x="20" y="42">AI agents, bots and forks '
               "excluded · archived repositories included</text>")
    if all_days:
        start = min(all_days)
        x = x_scale(start, today)
        years = range(academic_year(start), academic_year(today) + 1)
        per_year = {ay: [d for d in all_days if academic_year(d) == ay] for ay in years}
        y, step, ymax = y_scale(top, h, max(len(v) for v in per_year.values()))
        draw_grid(out, y, step, ymax)
        bounds = [date(ay, 9, 1) for ay in years if date(ay, 9, 1) > start]
        bounds += [b - timedelta(days=1) for b in bounds]
        keys, times = playback(all_days, start, today, bounds)
        when = dict(zip(keys, times))
        mo = Motion()
        marks: list[str] = []
        tip, tip_t, labels, label_t = [], [], [], []
        for ay in years:
            seg_start = max(start, date(ay, 9, 1))
            seg_end = min(today, date(ay + 1, 8, 31))
            if seg_start != start:  # year boundary: divider
                out.append(f'<line class="grid" x1="{x(seg_start):.1f}" x2="{x(seg_start):.1f}" '
                           f'y1="{top - 8}" y2="{y(0):.1f}" stroke-width="1" stroke-dasharray="3 3"/>')
            out.append(f'<text class="small t2" x="{x(seg_start) + 6:.1f}" y="{top - 4}">'
                       f'{ay_label(ay)} · {len(per_year[ay]):,}</text>')
            ks = [k for k in keys if seg_start <= k <= seg_end]
            ts = [when[k] for k in ks]
            cum = counts_at(per_year[ay], ks)
            pts = [(x(k), y(c)) for k, c in zip(ks, cum)]
            path = " ".join(f"{px:.1f},{py:.1f}" for px, py in pts)
            width = max(x(seg_end) - x(seg_start), 0.1)
            wipe = mo.add([(t, f"clip-path:inset(0 {100 - 100 * (px - x(seg_start)) / width:.1f}% 0 0)")
                           for t, (px, _) in zip(ts, pts)])
            marks.append(f'<polygon class="area {wipe}" points="{x(seg_start):.1f},{y(0):.1f} {path} '
                         f'{x(seg_end):.1f},{y(0):.1f}"/>')
            draw_line(marks, mo, "total", pts, ts)
            if tip:                 # jump back to zero instead of sliding down
                tip.append(pts[0])
                tip_t.append(ts[0] - 1e-5)
            first = {k: min(d for d in v if academic_year(d) == ay) for k, v in dates.items()
                     if any(academic_year(d) == ay for d in v)}
            people = counts_at(list(first.values()), ks)
            tip += pts
            tip_t += ts
            for (c, n), t in zip(*ticking(list(zip(cum, people)), ts)):
                label_t.append(t)
                labels.append(f'<tspan class="val t1">{c:,}</tspan><tspan class="small t2"> commits · '
                              f'{n} {"person" if n == 1 else "people"}</tspan>')
        marks.append(f'<g {moving(mo, tip, tip_t)}><circle class="totalf ring" r="4" stroke-width="2"/>')
        draw_counter(marks, mo, labels, label_t, 10, "start")
        marks.append("</g>")
        out += [WINDOW, mo.style(), '<g class="fade">', *marks, "</g>"]
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


FADED = 0.3                    # lines of people who are outside the top at that moment


def top_over_time(window: dict[str, list[date]], keys: list[date]) -> list[list[str]]:
    """The top contributors on each key date, best first (ties: whoever got there first)."""
    counts: dict[str, int] = {}
    since: dict[str, date] = {}
    events = sorted((d, k) for k, v in window.items() for d in v)
    tops, i = [], 0
    for key in keys:
        while i < len(events) and events[i][0] <= key:
            d, k = events[i]
            counts[k] = counts.get(k, 0) + 1
            since[k] = d
            i += 1
        tops.append(sorted(counts, key=lambda k: (-counts[k], since[k], k.lower()))[:TOP_N])
    return tops


def render_year_svg(dates: dict[str, list[date]], ay: int, today: date) -> tuple[str, list]:
    """Top contributors within one academic year (1 Sep ay - 31 Aug ay+1), as they changed."""
    start, end = date(ay, 9, 1), date(ay + 1, 8, 31)
    last = min(end, today)                   # the current year's lines stop today
    window = {k: [d for d in v if start <= d <= end] for k, v in dates.items()}
    window = {k: v for k, v in window.items() if v}

    # Everyone who was in the top at some point gets a line
    span = [start + timedelta(days=n) for n in range((last - start).days + 1)]
    daily = top_over_time(window, span)
    ranked = daily[-1]                       # the top today (or at the end of the year)
    stay: dict[str, int] = {}                # days spent in the top, in order of first entry
    for top_then in daily:
        for k in top_then:
            stay[k] = stay.get(k, 0) + 1
    past = sorted((k for k in stay if k not in ranked), key=lambda k: -stay[k])
    people = [*ranked, *past]
    hued = set(people[:len(SERIES_LIGHT)])   # the rest share a neutral gray
    slot = {k: i for i, k in enumerate(k for k in stay if k in hued)}
    color = {k: slot.get(k, len(SERIES_LIGHT)) for k in people}

    # Legend (always present for >= 2 series), wrapping onto extra rows if needed
    names = [(color[k], k) for k in people if k in hued]
    if len(hued) < len(people):
        names.append((len(SERIES_LIGHT), f"{len(people) - len(hued)} others"))
    legend, lx, row = [], 20, 0
    for c, name in names:
        w = 20 + 7.0 * len(name) + 18
        if lx + w > W - 10 and lx > 20:
            lx, row = 20, row + 1
        legend.append((c, name, lx, 24 + 20 * row))
        lx += w
    top, h = 54 + 20 * row, 220
    H = top + h + 40
    out = svg_open(H, f"How the top {TOP_N} contributors to {GH_ORG} changed during academic year "
                      f"{ay_label(ay)}. Final ranking: "
                      + ", ".join(f"{k} {len(window[k])}" for k in ranked))
    for c, name, lx, ty in legend:
        out.append(f'<rect class="f{c}" x="{lx:.0f}" y="{ty - 6}" width="14" height="4" rx="2"/>')
        out.append(f'<text class="small t1" x="{lx + 20:.0f}" y="{ty}">{escape(name)}</text>')

    axis_end = max(last, start + timedelta(days=30))   # a year in progress fills the chart
    x = x_scale(start, axis_end)
    y, step, ymax = y_scale(top, h, max(len(window[k]) for k in ranked))
    draw_grid(out, y, step, ymax)
    keys, times = playback([d for k in people for d in window[k]], start, last)
    tops = top_over_time(window, keys)
    cums = {k: counts_at(window[k], keys) for k in people}
    spots = {k: [(x(d), y(c)) for d, c in zip(keys, cums[k])] for k in people}
    mo = Motion()

    def visible(k: str, off: float) -> str:
        a = mo.add([(t, f"opacity:{1 if k in now else off}") for t, now in zip(times, tops)])
        return f'class="{a}" opacity="{1 if k in ranked else off}"'

    marks: list[str] = []
    for k in reversed(people):               # current leaders are drawn last, on top
        f0 = max(0, next(f for f, c in enumerate(cums[k]) if c) - 1)
        marks.append(f"<g {visible(k, FADED)}>")
        draw_line(marks, mo, f"s{color[k]}", spots[k][f0:], times[f0:])
        marks.append("</g>")
    for k in reversed(people):
        marks.append(f'<g {visible(k, 0)}><g {moving(mo, spots[k], times)}>'
                     f'<circle class="f{color[k]} ring" r="4" stroke-width="2"/></g></g>')
    # Labels follow the dots, swapping places as people overtake each other
    label = {k: [p[1] for p in spots[k]] for k in people}
    for f, now in enumerate(tops):
        for k, ly in zip(now, spread([spots[k][f][1] for k in now], top, top + h)):
            label[k][f] = ly
    num = round(7.2 * len(f"{max(c[-1] for c in cums.values()):,}"))
    for k in people:
        path = [(x(d), ly) for d, ly in zip(keys, label[k])]
        marks.append(f'<g {visible(k, 0)}><g {moving(mo, path, times)}>'
                     f'<rect class="f{color[k]}" x="10" y="-5" width="4" height="10" rx="2"/><g class="val t1">')
        vals, ts = ticking([(c,) for c in cums[k]], times)
        draw_counter(marks, mo, [f"{v[0]:,}" for v in vals], ts, 18 + num, "end")
        marks.append(f'</g><text class="small t2" x="{24 + num}" y="4">{escape(k)}</text></g></g>')
    out += [WINDOW, mo.style(), '<g class="fade">', *marks, "</g>"]
    draw_x_axis(out, top + h, x, time_ticks(start, axis_end), "%b %y")
    out.append("</svg>")
    return "\n".join(out) + "\n", [(k, len(window[k])) for k in people]


def write_chart(name: str, svg: str) -> None:
    """Save the animated chart and a still copy for visitors who prefer reduced motion.

    Browsers don't reliably pass that preference into <img>, so the README picks
    the still copy itself with <picture>.
    """
    (STATS / f"{name}.svg").write_text(svg)
    a = svg.find("<style>@media (prefers-reduced-motion")
    b = svg.find("</style>", a) + len("</style>")
    (STATS / f"{name}-static.svg").write_text(svg[:a] + svg[b:] if a >= 0 else svg)


def picture(name: str, alt: str) -> str:
    return (f'<picture><source media="(prefers-reduced-motion: reduce)" srcset="{RAW}/stats/{name}-static.svg">'
            f'<img src="{RAW}/stats/{name}.svg" alt="{alt}" width="760"/></picture>')


def year_sections(years: list[int], today: date) -> str:
    """Collapsible <details> per academic year, newest first and open."""
    blocks = []
    for n, ay in enumerate(sorted(years, reverse=True)):
        slug = ay_label(ay).replace("/", "-")
        note = " · in progress" if ay == academic_year(today) else ""
        blocks.append(
            f'<details{" open" if n == 0 else ""}>\n'
            f"<summary><b>{ay_label(ay)}</b>{note}</summary>\n<br/>\n"
            f'{picture(f"top-{slug}", f"How the top contributors changed during {ay_label(ay)}")}\n'
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
    write_chart("activity", render_org_svg(dates, today))

    years = sorted({academic_year(d) for v in dates.values() for d in v})
    for old in STATS.glob("top-*.svg"):
        old.unlink()
    for ay in years:
        svg, top = render_year_svg(dates, ay, today)
        write_chart(f"top-{ay_label(ay).replace('/', '-')}", svg)
        print(ay_label(ay), top)
    update_readme(year_sections(years, today))


if __name__ == "__main__":
    main()
