"""State persistence.

State shape: { "<vin>": {"price": int, "dealer": str, "first_seen": iso8601, "last_seen": iso8601} }
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)

STATE_FILE = "state.json"


def load(state_root: Path) -> dict:
    """Load state.json. Raises on read/parse/shape errors so the caller can fail
    loud rather than silently treating a bad file as "no state" (which would
    re-flag every current VIN as new on the next run).

    Raises:
        OSError: file read error.
        json.JSONDecodeError: malformed JSON.
        ValueError: JSON parses but isn't the expected {vin: {...}} dict shape.
    """
    path = state_root / STATE_FILE
    if not path.exists():
        log.info("state file missing at %s; starting empty", path)
        return {}
    with path.open() as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(
            f"state.json has wrong shape: expected dict, got {type(data).__name__}"
        )
    return data


def save(state_root: Path, state: dict) -> None:
    path = state_root / STATE_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    with tmp.open("w") as f:
        json.dump(state, f, indent=2, sort_keys=True)
    tmp.replace(path)
    log.info("state saved: %d vins", len(state))
