# .github

Organization profile for [iaac-maai](https://github.com/iaac-maai). The page shown on the org's GitHub front page is [`profile/README.md`](profile/README.md).

- `scripts/update_stats.py` writes `stats/hf.json` (Hugging Face downloads and likes totals, read by the badges) and `stats/contributors.svg` (top contributors chart).
- `.github/workflows/update-stats.yml` runs it every day at 05:00 UTC and on manual dispatch.
