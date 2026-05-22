"""Per-listing filters. A listing must match ALL non-None filters to pass.

Applied at the fetch boundary (in `__main__.py`) so the state.json only ever
contains VINs that match the user's shopping criteria. Filtering after-the-fact
would mean tracking hundreds of irrelevant VINs in state forever.

Tradeoff: if you widen these filters later, the previously-filtered listings
will look "new" on the next run. The `MAX_NEW_LISTINGS_PER_RUN` safety cap in
__main__.py catches that flood — investigate, then re-bootstrap with force=true
to capture the new snapshot.

To disable a filter, set its constant to `None`.
"""
from __future__ import annotations

# Skip listings older than this model year.
MIN_YEAR: int | None = 2024
# Skip listings with more than this many miles. Listings whose mileage is
# missing/None are also skipped — better to under-alert than to surprise on
# unknown-mileage records.
MAX_MILEAGE: int | None = 25_000
# Exact model match, case-insensitive. Toyota's API returns canonical model
# names ("Camry", "RAV4", "Tacoma") so exact match is reliable.
MODEL: str | None = "Camry"
# Exact trim match, case-insensitive. Some dealers append qualifiers to trim
# (e.g. "LE Sedan FWD"), but the inventory APIs themselves return the clean
# trim string ("LE"). If a dealer ever returns a verbose trim, switch to
# substring match (see commented-out alternative in `matches()`).
TRIM: str | None = "LE"


def matches(listing: dict) -> bool:
    """Return True if the listing passes every active filter."""
    if MIN_YEAR is not None:
        year = listing.get("year")
        if year is None or year < MIN_YEAR:
            return False

    if MAX_MILEAGE is not None:
        mileage = listing.get("mileage")
        # None mileage = unknown; we reject rather than risk surprise alerts.
        if mileage is None or mileage > MAX_MILEAGE:
            return False

    if MODEL is not None:
        model = (listing.get("model") or "").strip().lower()
        if model != MODEL.strip().lower():
            return False

    if TRIM is not None:
        trim = (listing.get("trim") or "").strip().lower()
        if trim != TRIM.strip().lower():
            # Alternative if a dealer ever returns verbose trim:
            #   if TRIM.lower() not in trim:
            return False

    return True
