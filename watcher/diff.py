"""Compute changes between previous state and current listings.

Returns:
    new_listings: VINs that weren't in the previous state
    price_drops: VINs we've seen before whose price is now lower (list of
                 (listing, old_price) tuples)
    next_state: updated state dict to persist
"""
from __future__ import annotations

from datetime import datetime, timezone


def compute(prev_state: dict, current: list[dict]) -> tuple[list[dict], list[tuple[dict, int]], dict]:
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    new_listings: list[dict] = []
    price_drops: list[tuple[dict, int]] = []
    next_state: dict = {}
    for listing in current:
        vin = listing["vin"]
        price = listing.get("price")
        if vin not in prev_state:
            new_listings.append(listing)
            next_state[vin] = {
                "price": price,
                "dealer": listing.get("dealer_key"),
                "first_seen": now_iso,
                "last_seen": now_iso,
            }
            continue
        prev = prev_state[vin]
        prev_price = prev.get("price")
        if price is not None and prev_price is not None and price < prev_price:
            price_drops.append((listing, prev_price))
        next_state[vin] = {
            "price": price if price is not None else prev_price,
            "dealer": listing.get("dealer_key") or prev.get("dealer"),
            "first_seen": prev.get("first_seen", now_iso),
            "last_seen": now_iso,
        }
    return new_listings, price_drops, next_state
