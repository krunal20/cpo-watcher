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

### 2. (No bootstrap required)

Just enable the workflow. On the first scheduled tick, every current matching listing is treated as "new" and emailed. With the default filters (Camry LE, 2024+, ≤25k miles) the first email will typically contain a small handful of cards.

If you'd rather seed `state.json` silently and only get emails on *subsequent* changes, run the workflow once manually with `bootstrap=true` — that records the current snapshot without emailing.

## Adding dealers later

Edit [watcher/dealers.py](watcher/dealers.py) and push to `main`. On the next scheduled run, that dealer's matching VINs flow in as "new" and trigger an email. If the new dealer happens to add more than 50 matching listings at once, the safety cap below will block the run — investigate the per-dealer breakdown in the error log, then re-run the workflow manually with `force=true` to release the email.

## Safety guards

The watcher hard-fails (exit 1) rather than risk an alert flood when any of these tripwires fire:

- **State file is corrupt / unreadable** — won't silently fall back to "no state" (which would mass-alert). Inspect the `watch-state` branch and fix manually.
- **Bootstrap requested with non-empty state** — would reset every `first_seen` timestamp. Pass `force=true` if intentional.
- **More than `MAX_NEW_LISTINGS_PER_RUN` (50) new VINs in one cycle** — usually means an API change or inventory swap, not 50 genuinely new cars in 15 minutes. The error log includes a per-dealer breakdown. Investigate first, then re-run with `force=true` to send the email.
- **More than `MAX_PRICE_DROPS_PER_RUN` (30) price drops in one cycle** — usually means a dealer flipped pricing fields (e.g. internetPrice → salePrice) rather than 30 genuine drops. Same `force=true` escape hatch.
- **First run (empty state) with any dealer failure** — baselining on a partial fetch would cause a false-alert flood when the failed dealer recovers. Investigate the dealer error first, then re-run with `force=true` (or `bootstrap=true` to snapshot the partial state silently). On a clean first run where all 5 dealers respond, this guard never fires and the email goes out normally.

## How it works

- `*/15 * * * *` cron triggers the workflow.
- Workflow checks out `main` (code) and `watch-state` (`state.json`) into separate paths.
- Python script polls each dealer with 10s spacing (respecting the documented crawl delay), retries with backoff on transient errors, and won't fail the whole run if one dealer is unreachable (its prior VINs are carried forward so recovery doesn't trigger a false-alert flood).
- Computes new VINs + price drops vs previous state.
- Sends one consolidated email if anything changed, then commits the updated state file back to `watch-state`. If there were changes and the SMTP send failed, state is *not* saved — so the next cron tick simply retries the same alert. (When there are no changes, state is still persisted to update `last_seen` timestamps.)

## Adjusting

- **Add or remove dealers**: edit `watcher/dealers.py`.
- **Change cadence**: edit the `cron:` line in `.github/workflows/watch.yml`. The GitHub Free plan gives 2,000 Actions minutes/month pooled across all your private repos; public repos have unlimited minutes on standard runners.
- **Test locally**: `pip install -r requirements.txt && python -m watcher --state-root ./local-state --dry-run --no-jitter`.

## Notes

- All credentials live in GitHub Secrets — never in code.
- Action run logs include VINs and prices (derived from publicly accessible inventory pages); the sender email address and SMTP user are masked at the runner level.
- The `watch-state` branch is committed to publicly; this is intentional so state persists across runs without extra infrastructure. The data committed is derived from public dealer inventory.
