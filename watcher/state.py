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
    path = state_root / STATE_FILE
    if not path.exists():
        log.info("state file missing at %s; starting empty", path)
        return {}
    try:
        with path.open() as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        log.error("failed to load state from %s: %r; starting empty", path, e)
        return {}


def save(state_root: Path, state: dict) -> None:
    path = state_root / STATE_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    with tmp.open("w") as f:
        json.dump(state, f, indent=2, sort_keys=True)
    tmp.replace(path)
    log.info("state saved: %d vins", len(state))
