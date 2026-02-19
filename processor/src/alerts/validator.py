from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import jsonschema

# Load F-7 alert contract schema once at import time — zero cost in the hot path.
# Path: processor/src/alerts/validator.py → parents[3] = repo root
#       → schema/contracts/alert_payload.json
_SCHEMA: dict[str, Any] = json.loads(
    (Path(__file__).parents[3] / "schema" / "contracts" / "alert_payload.json").read_text()
)


def validate_payload(payload: dict[str, Any]) -> None:
    """
    Validate an alert payload against the F-7 alert_payload.json contract.

    Raises jsonschema.ValidationError if the payload does not conform.
    Called before DB write so contract violations are caught immediately
    at fire time, not silently persisted.
    """
    jsonschema.validate(payload, _SCHEMA)
