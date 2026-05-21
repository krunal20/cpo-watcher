"""Entry point: python -m watcher --state-root <path>

Polls all dealers, diffs against the last state, persists the new state, and
emails on changes. Designed to run from GitHub Actions on a cron.

Exit codes:
  0  success — including partial-dealer failures (each surfaced as a GitHub
     ::warning:: annotation, but the run stays green because state was correctly
     persisted with the failed dealers' VINs carried forward from the previous
     run to prevent a false "new VIN" flood on recovery)
  1  hard failure (no dealers reachable, or notification send failed)
"""
from __future__ import annotations

import argparse
import logging
import random
import sys
import time
from pathlib import Path

import requests

from watcher import diff, notify, state
from watcher.dealers import DEALERS
from watcher.fetchers import fetch_with_retry

log = logging.getLogger("watcher")

# Time to wait between hitting different dealers, in seconds. DealerOn's robots.txt
# explicitly requests Crawl-delay: 10.
DEALER_GAP_SECONDS = 10
# Random jitter at start of run to avoid clustering exactly on :00/:15/:30/:45 with
# every other GitHub Actions cron job in the world.
MAX_JITTER_SECONDS = 30


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--state-root", required=True, type=Path, help="path to the state directory (state.json lives here)")
    p.add_argument("--no-jitter", action="store_true", help="skip startup jitter (useful in tests)")
    p.add_argument("--dry-run", action="store_true", help="don't write state, don't send email")
    p.add_argument("--bootstrap", action="store_true", help="treat all current listings as already-seen (no email)")
    return p.parse_args()


def fetch_all(session: requests.Session) -> tuple[list[dict], set[str]]:
    """Fetch every dealer. Returns (all_listings, failed_dealer_keys)."""
    listings: list[dict] = []
    failed: set[str] = set()
    for i, dealer in enumerate(DEALERS):
        if i > 0:
            log.info("sleeping %ds before next dealer", DEALER_GAP_SECONDS)
            time.sleep(DEALER_GAP_SECONDS)
        dealer_listings = fetch_with_retry(dealer, session)
        if not dealer_listings:
            # We can't disambiguate "dealer truly has 0 CPO" from "API failed silently".
            # The safer interpretation is failure: a real-world dealer with a CPO page
            # is almost never at exactly 0. Treat as failure so we carry forward
            # rather than mass-alerting on recovery.
            failed.add(dealer["key"])
        listings.extend(dealer_listings)
    return listings, failed


def carry_forward_failed(prev: dict, next_state: dict, failed_dealer_keys: set[str]) -> int:
    """For dealers that failed this run, copy their previous-state entries forward so
    their VINs aren't seen as 'new' next time. Returns the number of carried entries."""
    carried = 0
    for vin, data in prev.items():
        if data.get("dealer") in failed_dealer_keys and vin not in next_state:
            next_state[vin] = data
            carried += 1
    return carried


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s | %(message)s",
        stream=sys.stdout,
    )
    args = parse_args()

    if not args.no_jitter:
        jitter = random.uniform(0, MAX_JITTER_SECONDS)
        log.info("startup jitter: sleeping %.1fs", jitter)
        time.sleep(jitter)

    prev = state.load(args.state_root)
    log.info("loaded prev state: %d vins", len(prev))

    session = requests.Session()
    current, failed = fetch_all(session)
    log.info("fetched total listings: %d (failed dealers: %s)",
             len(current), sorted(failed) or "none")
    for key in sorted(failed):
        # GitHub Actions surfaces these as yellow warnings on the run summary.
        print(f"::warning::dealer {key} returned no inventory this run "
              f"(state carried forward from previous run)")

    if not current:
        print("::error::all dealers failed; not touching state, not sending email")
        return 1

    if args.bootstrap:
        _, _, next_state = diff.compute({}, current)
        carried = carry_forward_failed(prev, next_state, failed)
        if carried:
            log.info("bootstrap: carried %d prev VINs forward for failed dealers", carried)
        log.info("bootstrap mode: recording %d vins, not emailing", len(next_state))
        if args.dry_run:
            log.info("dry-run: would save %d vins", len(next_state))
            return 0
        state.save(args.state_root, next_state)
        return 0

    new_listings, price_drops, next_state = diff.compute(prev, current)
    carried = carry_forward_failed(prev, next_state, failed)
    if carried:
        log.info("carried %d prev VINs forward for failed dealers", carried)
    log.info("diff: new=%d price_drops=%d", len(new_listings), len(price_drops))

    if args.dry_run:
        log.info("dry-run: would save %d vins, would email new=%d drops=%d",
                 len(next_state), len(new_listings), len(price_drops))
        return 0

    # Email first; only persist state if the email was either sent successfully or
    # there was nothing to send. If SMTP fails we exit 1 *without* saving so the
    # next run retries the alert.
    if new_listings or price_drops:
        ok = notify.send(new_listings, price_drops)
        if not ok:
            log.error("notification failed; not saving state so the next run retries")
            return 1

    state.save(args.state_root, next_state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
