"""Entry point: python -m watcher --state-root <path>

Polls all dealers, diffs against the last state, persists the new state, and
emails on changes. Designed to run from GitHub Actions on a cron.

Exit codes:
  0  success — including partial-dealer failures (each surfaced as a GitHub
     ::warning:: annotation, but the run stays green because state was correctly
     persisted with the failed dealers' VINs carried forward from the previous
     run to prevent a false "new VIN" flood on recovery)
  1  hard failure: no dealers reachable, notification send failed, corrupt /
     wrong-shape state file, or a safety guard tripped (oversized new-listings
     or price-drops batch, bootstrap that would wipe existing state, or first
     run with partial dealer failure). Tripped guards can be overridden with
     `--force` after manual investigation.
"""
from __future__ import annotations

import argparse
import json
import logging
import random
import sys
import time
from pathlib import Path

import requests

from watcher import diff, filters, notify, state
from watcher.dealers import DEALERS
from watcher.fetchers import fetch_with_retry

log = logging.getLogger("watcher")

# Time to wait between hitting different dealers, in seconds. DealerOn's robots.txt
# explicitly requests Crawl-delay: 10.
DEALER_GAP_SECONDS = 10
# Random jitter at start of run to avoid clustering exactly on :00/:15/:30/:45 with
# every other GitHub Actions cron job in the world.
MAX_JITTER_SECONDS = 30
# Safety cap on "new VINs in one run". CPO inventory turns over slowly; a normal
# day sees <10 new listings across all 5 dealers. Anything over this is a strong
# signal that something is wrong (dealer platform migration, swapped inventory,
# scope-of-filter regression). Override with --force after investigating.
MAX_NEW_LISTINGS_PER_RUN = 50
# Same idea for price-drops: a single dealer flipping pricing fields (e.g. swapping
# from internetPrice to salePrice) can produce dozens of phantom drops in one cycle.
# Cap separately because new-listing storms and drop storms have different root causes.
MAX_PRICE_DROPS_PER_RUN = 30


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--state-root", required=True, type=Path, help="path to the state directory (state.json lives here)")
    p.add_argument("--no-jitter", action="store_true", help="skip startup jitter (useful in tests)")
    p.add_argument("--dry-run", action="store_true", help="don't write state, don't send email")
    p.add_argument("--bootstrap", action="store_true", help="treat all current listings as already-seen (no email)")
    p.add_argument("--force", action="store_true",
                   help="override safety guards: allow bootstrap to overwrite existing state, "
                        "or allow an email exceeding MAX_NEW_LISTINGS_PER_RUN. Use only after "
                        "investigating why the guard tripped.")
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

    try:
        prev = state.load(args.state_root)
    except (json.JSONDecodeError, OSError, ValueError) as e:
        # Corrupt / unreadable / wrong-shape state file. Bail out loudly so the
        # user notices and investigates — silently treating it as "no state"
        # would re-flag every current VIN as new and trigger a flood email.
        print(f"::error::state file unreadable ({type(e).__name__}): {e}. "
              "Inspect the watch-state branch manually before re-running.")
        return 1
    log.info("loaded prev state: %d vins", len(prev))

    # Note: empty prev state in non-bootstrap mode is allowed. On first run (or
    # after adding a new dealer in dealers.py), every matching VIN is "new" and
    # will be emailed. The MAX_NEW_LISTINGS_PER_RUN guard below still catches a
    # genuine flood. `--bootstrap` remains an opt-in for silent snapshotting.

    # Guard: bootstrap mode with a non-empty existing state would silently reset
    # every VIN's `first_seen` timestamp. Require --force to override.
    if args.bootstrap and prev and not args.force:
        print(f"::error::--bootstrap requested but state already contains {len(prev)} VINs. "
              "Re-running bootstrap would reset every `first_seen` timestamp. "
              "Pass --force (workflow input `force=true`) if that's intentional.")
        return 1

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

    # Apply user filters (see watcher/filters.py for the active criteria).
    # Filtering at fetch boundary keeps state.json scoped to just the listings
    # the user actually wants alerts about. The carry-forward set is filtered
    # too: a non-matching VIN that was previously tracked falls off naturally.
    pre_filter = len(current)
    current = [l for l in current if filters.matches(l)]
    log.info("after filters: %d matching (filtered out %d)", len(current), pre_filter - len(current))

    # Guard: first run (empty prev) with any dealer failure in non-bootstrap mode
    # would baseline state to only the succeeding dealers — guaranteeing that when
    # the failed dealers recover, every matching VIN on them shows up as "new" and
    # floods the email (or trips the MAX cap, requiring repeated --force). Skip in
    # bootstrap mode since that path is explicit "snapshot whatever you fetched".
    if not prev and failed and not args.bootstrap and not args.force:
        print(f"::error::first run (empty state) but {len(failed)} dealer(s) failed: "
              f"{sorted(failed)}. Baselining on a partial fetch would cause a false-alert "
              "flood when the failed dealer(s) recover. Investigate the dealer error(s) "
              "above, then re-run with force=true (or use bootstrap=true to silently "
              "snapshot the partial state).")
        return 1

    new_listings, price_drops, next_state = diff.compute(prev, current)
    carried = carry_forward_failed(prev, next_state, failed)
    if carried:
        log.info("carried %d prev VINs forward for failed dealers", carried)
    log.info("diff: new=%d price_drops=%d", len(new_listings), len(price_drops))

    # Guards: oversized "new" or "drop" batches are almost always symptoms (platform
    # migration, filter regression, swapped inventory, pricing-field flip) rather
    # than that many genuine changes in 15 minutes. We check these BEFORE the
    # dry-run branch so dry-runs accurately reflect what a real run would do, and
    # BEFORE bootstrap so a misconfigured bootstrap can't silently snapshot garbage.
    if not args.force:
        if args.bootstrap:
            # In bootstrap mode the "new" count is effectively len(current) since
            # prev is treated as empty for the diff. Check against the cap.
            if len(current) > MAX_NEW_LISTINGS_PER_RUN:
                per_dealer = _per_dealer_count(current)
                print(f"::error::bootstrap would snapshot {len(current)} listings, exceeding "
                      f"safety cap ({MAX_NEW_LISTINGS_PER_RUN}). per-dealer: {per_dealer}. "
                      "This usually indicates a misconfigured filter or dealer entry. "
                      "Investigate, then re-run with force=true to override.")
                return 1
        else:
            if len(new_listings) > MAX_NEW_LISTINGS_PER_RUN:
                per_dealer = _per_dealer_count(new_listings)
                print(f"::error::{len(new_listings)} new listings exceeds safety cap "
                      f"({MAX_NEW_LISTINGS_PER_RUN}). per-dealer: {per_dealer}. "
                      "This usually indicates an inventory swap or filter regression. "
                      "Investigate, then re-run with force=true to send the email.")
                return 1
            if len(price_drops) > MAX_PRICE_DROPS_PER_RUN:
                per_dealer = _per_dealer_count(l for l, _ in price_drops)
                print(f"::error::{len(price_drops)} price drops exceeds safety cap "
                      f"({MAX_PRICE_DROPS_PER_RUN}). per-dealer: {per_dealer}. "
                      "This usually indicates a pricing-field flip on a dealer, not real "
                      "drops. Investigate, then re-run with force=true to send the email.")
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


def _per_dealer_count(listings) -> dict:
    out: dict[str, int] = {}
    for l in listings:
        k = l.get("dealer_key") or "?"
        out[k] = out.get(k, 0) + 1
    return out


if __name__ == "__main__":
    raise SystemExit(main())
