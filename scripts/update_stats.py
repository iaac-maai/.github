"""Refresh the stats shown on the iaac-maai GitHub organization page.

Writes:
  stats/hf.json            totals for the iaac-maai Hugging Face org (read by shields.io badges)
  stats/contributors.svg   bar chart of the top human contributors across public org repos

Standard library only. Set GITHUB_TOKEN to avoid API rate limits.
"""

from __future__ import annotations

import base64
import json
import os
import re
import urllib.request
from datetime import datetime, timezone
from html import escape
from pathlib import Path

HF_ORG = "iaac-maai"
GH_ORG = "iaac-maai"
TOP_N = 15
ROOT = Path(__file__).resolve().parent.parent
STATS = ROOT / "stats"

# AI agents and automation accounts never count as contributors.
# (GitHub's contributors API counts commit authors only, so Co-Authored-By
# trailers never add anyone; this list covers agents that author commits.)
EXCLUDED_LOGINS = {
    "claude", "anthropic-claude", "claude-code", "copilot", "copilot-swe-agent",
    "devin-ai-integration", "cursor-agent", "cursoragent", "github-actions",
    "dependabot", "renovate", "openai-codex", "codex", "chatgpt-codex-connector",
    "sweep-ai", "aider", "gemini-code-assist", "google-labs-jules",
}
EXCLUDED_PATTERN = re.compile(r"(\[bot\]|bot|agent|ai-assistant)$", re.IGNORECASE)


def get_json(url: str, token: str | None = None):
    headers = {"User-Agent": "iaac-maai-org-stats", "Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=60) as r:
        body = r.read()
        return json.loads(body) if body else []


def get_bytes(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "iaac-maai-org-stats"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


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
def is_human(c: dict) -> bool:
    login = (c.get("login") or "").lower()
    if not login or c.get("type") != "User":
        return False
    return login not in EXCLUDED_LOGINS and not EXCLUDED_PATTERN.search(login)


def gh_contributors(token: str | None) -> list[dict]:
    repos, page = [], 1
    while True:
        batch = get_json(f"https://api.github.com/orgs/{GH_ORG}/repos"
                         f"?type=public&per_page=100&page={page}", token)
        repos += batch
        if len(batch) < 100:
            break
        page += 1

    totals: dict[str, dict] = {}
    for repo in repos:
        if repo.get("private") or repo.get("fork") or repo.get("archived"):
            continue
        if repo["name"] == ".github":  # the stats bot commits here
            continue
        page = 1
        while True:
            batch = get_json(f"https://api.github.com/repos/{GH_ORG}/{repo['name']}"
                             f"/contributors?per_page=100&page={page}", token)
            for c in batch or []:
                if not is_human(c):
                    continue
                entry = totals.setdefault(c["login"], {
                    "login": c["login"], "avatar": c["avatar_url"],
                    "commits": 0, "repos": 0})
                entry["commits"] += c["contributions"]
                entry["repos"] += 1
            if not batch or len(batch) < 100:
                break
            page += 1

    ranked = sorted(totals.values(), key=lambda e: (-e["commits"], e["login"].lower()))
    return ranked[:TOP_N]


# -------------------------------------------------------------------------- SVG
def avatar_data_uri(url: str) -> str:
    sep = "&" if "?" in url else "?"
    try:
        data = get_bytes(f"{url}{sep}s=64")
        mime = "image/jpeg" if data[:2] == b"\xff\xd8" else "image/png"
        return f"data:{mime};base64," + base64.b64encode(data).decode()
    except Exception:
        return ""


def render_svg(people: list[dict]) -> str:
    width, row_h, top, left_label = 760, 34, 64, 230
    bar_x, bar_max = left_label + 10, width - left_label - 90
    height = top + max(len(people), 1) * row_h + 34
    peak = max((p["commits"] for p in people), default=1)
    updated = datetime.now(timezone.utc).strftime("%d %b %Y")

    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" role="img" '
        f'aria-label="Top {len(people)} contributors to the {GH_ORG} organization">',
        "<style>",
        "  .bg{fill:#ffffff} .title{fill:#1f2328;font:600 18px -apple-system,Segoe UI,Helvetica,Arial,sans-serif}",
        "  .sub,.foot{fill:#59636e;font:12px -apple-system,Segoe UI,Helvetica,Arial,sans-serif}",
        "  .name{fill:#1f2328;font:500 13px -apple-system,Segoe UI,Helvetica,Arial,sans-serif}",
        "  .rank{fill:#59636e;font:12px ui-monospace,SFMono-Regular,Menlo,monospace}",
        "  .val{fill:#1f2328;font:600 12px -apple-system,Segoe UI,Helvetica,Arial,sans-serif}",
        "  .bar{fill:#e8a400} .bar.top{fill:#ff9d00} .track{fill:#f0f2f4}",
        "  @media (prefers-color-scheme: dark){",
        "    .bg{fill:#0d1117} .title,.name,.val{fill:#e6edf3} .sub,.foot,.rank{fill:#9198a1}",
        "    .bar{fill:#d29a1c} .bar.top{fill:#ffd21e} .track{fill:#161b22}",
        "  }",
        "</style>",
        f'<rect class="bg" width="{width}" height="{height}" rx="10"/>',
        f'<text class="title" x="20" y="32">Top contributors</text>',
        f'<text class="sub" x="20" y="50">Commits across public {GH_ORG} repositories '
        "· AI agents and bots excluded</text>",
        "<defs>",
    ]
    for i, _ in enumerate(people):
        out.append(f'<clipPath id="c{i}"><circle cx="0" cy="0" r="11"/></clipPath>')
    out.append("</defs>")

    for i, p in enumerate(people):
        y = top + i * row_h
        cy = y + row_h / 2
        bw = max(3, round(bar_max * p["commits"] / peak))
        cls = "bar top" if i < 3 else "bar"
        login = escape(p["login"])
        uri = avatar_data_uri(p["avatar"])
        out += [
            f'<g><title>{login}: {p["commits"]} commits in {p["repos"]} '
            f'repo{"s" if p["repos"] != 1 else ""}</title>',
            f'<text class="rank" x="20" y="{cy + 4:.0f}">{i + 1:>2}</text>',
        ]
        if uri:
            out.append(f'<g transform="translate(58 {cy:.0f})"><image href="{uri}" '
                       f'x="-11" y="-11" width="22" height="22" clip-path="url(#c{i})"/></g>')
        out += [
            f'<text class="name" x="78" y="{cy + 4:.0f}">{login}</text>',
            f'<rect class="track" x="{bar_x}" y="{cy - 7:.0f}" width="{bar_max}" height="14" rx="3"/>',
            f'<rect class="{cls}" x="{bar_x}" y="{cy - 7:.0f}" width="{bw}" height="14" rx="3"/>',
            f'<text class="val" x="{bar_x + bw + 8}" y="{cy + 4:.0f}">{p["commits"]}</text>',
            "</g>",
        ]
    if not people:
        out.append(f'<text class="sub" x="20" y="{top + 20}">No contributors yet.</text>')
    out.append(f'<text class="foot" x="20" y="{height - 14}">Updated {updated}</text>')
    out.append("</svg>")
    return "\n".join(out) + "\n"


def main() -> None:
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    STATS.mkdir(exist_ok=True)

    hf = hf_stats()
    (STATS / "hf.json").write_text(json.dumps(hf, indent=2) + "\n")
    print("HF:", hf)

    people = gh_contributors(token)
    (STATS / "contributors.svg").write_text(render_svg(people))
    print("Contributors:", [(p["login"], p["commits"]) for p in people])


if __name__ == "__main__":
    main()
