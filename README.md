# .github

Organization profile for [iaac-maai](https://github.com/iaac-maai). The page shown on the org's GitHub front page is [`profile/README.md`](profile/README.md).

- `scripts/update_stats.py` writes `stats/hf.json` (Hugging Face downloads and likes totals, read by the badges) and `stats/activity.svg` (org-wide cumulative commits) and one `stats/top-YYYY-YY.svg` per academic year (top 5 contributors, Sep–Aug); it also rewrites the per-year `<details>` sections in `profile/README.md` between the `years` markers.
- `.github/workflows/update-stats.yml` runs it every day at 05:00 UTC and on manual dispatch.
