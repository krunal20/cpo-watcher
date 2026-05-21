# cpo-watcher

Polls a small set of dealer inventory APIs every 15 minutes and emails when:

- a previously-unseen VIN appears, or
- the asking price drops on a VIN we've already tracked.

Runs entirely on GitHub Actions cron. State is persisted on a separate `watch-state` branch in this repo.

## One-time setup

### 1. Add repository secrets

`Settings → Secrets and variables → Actions → New repository secret`. Add three secrets:

| Name | Value |
|---|---|
| `GMAIL_USERNAME` | The sender Gmail address (e.g. `you@gmail.com`) |
| `GMAIL_APP_PASSWORD` | A 16-character App Password generated at <https://myaccount.google.com/apppasswords>. The Google account must have 2-Step Verification enabled. |
| `ALERT_RECIPIENT` | Email address that receives the alerts |

### 2. Bootstrap the inventory snapshot

The very first poll will see every current listing as "new" and try to email all of them. To avoid that, run the workflow once manually in **bootstrap mode** before letting the cron take over:

`Actions → cpo-watch → Run workflow → set "bootstrap" to true → Run`.

That run records every current VIN to `state.json` without sending any email. From then on, the scheduled cron sends alerts only for genuine changes.

## How it works

- `*/15 * * * *` cron triggers the workflow.
- Workflow checks out `main` (code) and `watch-state` (`state.json`) into separate paths.
- Python script polls each dealer with 10s spacing (respecting the documented crawl delay), retries with backoff on transient errors, and won't fail the whole run if one dealer is unreachable (its prior VINs are carried forward so recovery doesn't trigger a false-alert flood).
- Computes new VINs + price drops vs previous state.
- Sends one consolidated email if anything changed, then commits the updated state file back to `watch-state`. State is only persisted when the email send succeeds (or there was nothing to send), so a transient SMTP failure simply re-tries on the next cron tick.

## Adjusting

- **Add or remove dealers**: edit `watcher/dealers.py`.
- **Change cadence**: edit the `cron:` line in `.github/workflows/watch.yml`. The GitHub Free plan gives 2,000 Actions minutes/month pooled across all your private repos; public repos have unlimited minutes on standard runners.
- **Test locally**: `pip install -r requirements.txt && python -m watcher --state-root ./local-state --dry-run --no-jitter`.

## Notes

- All credentials live in GitHub Secrets — never in code.
- Action run logs include VINs and prices (derived from publicly accessible inventory pages); the sender email address and SMTP user are masked at the runner level.
- The `watch-state` branch is committed to publicly; this is intentional so state persists across runs without extra infrastructure. The data committed is derived from public dealer inventory.
