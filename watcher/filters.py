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
# Model name, case-insensitive. Matches the bare name OR the name followed by a
# powertrain qualifier — so MODEL="Camry" matches both "Camry" and "Camry Hybrid"
# (Toyota's API uses both forms inconsistently across dealers). It does NOT match
# unrelated models that happen to share a prefix (e.g. a hypothetical "Camrylike"
# wouldn't match because the next char after "Camry" must be whitespace).
MODEL: str | None = "Camry"
# Trim, case-insensitive, matched as a whitespace-separated token. So TRIM="LE"
# matches "LE", "LE Hybrid", "LE Sedan FWD", and Earnhardt's marketing-suffixed
# "LE *1-OWNER*" — but correctly rejects "XLE", "SE", or any trim where LE is a
# substring rather than a standalone word.
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
        target = MODEL.strip().lower()
        # Either an exact match ("Camry") or the model with a trailing qualifier
        # like "Camry Hybrid". Requires whitespace after the target to avoid
        # spurious prefix matches.
        if model != target and not model.startswith(target + " "):
            return False

    if TRIM is not None:
        trim_tokens = (listing.get("trim") or "").lower().split()
        if TRIM.strip().lower() not in trim_tokens:
            return False

    return True
