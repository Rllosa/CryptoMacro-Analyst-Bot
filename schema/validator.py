#!/usr/bin/env python3
"""
Schema Contract Validators

Validates payloads against JSON schemas defined in schema/contracts/.
Used in contract tests to prevent silent breaking changes across service boundaries.

Usage:
    from schema.validator import validate_nats_candle, validate_alert, ValidationError

    try:
        validate_nats_candle(candle_message)
    except ValidationError as e:
        print(f"Invalid candle message: {e}")
"""

import json
from pathlib import Path
from typing import Any, Dict

try:
    from jsonschema import validate as jsonschema_validate, ValidationError as JsonSchemaValidationError
    from jsonschema import Draft7Validator
except ImportError:
    raise ImportError(
        "jsonschema is required for schema validation. "
        "Install with: pip install jsonschema"
    )


# Export ValidationError for convenience
class ValidationError(JsonSchemaValidationError):
    """Schema validation error"""
    pass


# Path to schema contracts directory
SCHEMA_DIR = Path(__file__).parent / "contracts"


def _load_schema(schema_name: str) -> Dict[str, Any]:
    """Load a JSON schema from the contracts directory."""
    schema_path = SCHEMA_DIR / schema_name
    if not schema_path.exists():
        raise FileNotFoundError(f"Schema file not found: {schema_path}")

    with open(schema_path, "r") as f:
        return json.load(f)


def _validate_against_schema(payload: Dict[str, Any], schema: Dict[str, Any]) -> None:
    """
    Validate a payload against a schema.

    Raises:
        ValidationError: If validation fails
    """
    try:
        jsonschema_validate(instance=payload, schema=schema)
    except JsonSchemaValidationError as e:
        raise ValidationError(e.message) from e


# Schema cache to avoid repeated file reads
_SCHEMA_CACHE: Dict[str, Dict[str, Any]] = {}


def _get_schema(schema_name: str) -> Dict[str, Any]:
    """Get schema from cache or load it."""
    if schema_name not in _SCHEMA_CACHE:
        _SCHEMA_CACHE[schema_name] = _load_schema(schema_name)
    return _SCHEMA_CACHE[schema_name]


# Public validator functions for each schema type

def validate_nats_candle(payload: Dict[str, Any]) -> None:
    """
    Validate a NATS candle message payload.

    Args:
        payload: Candle message dictionary

    Raises:
        ValidationError: If validation fails
    """
    schema = _get_schema("nats_candle_message.json")
    _validate_against_schema(payload, schema)


def validate_alert(payload: Dict[str, Any]) -> None:
    """
    Validate an alert payload.

    Args:
        payload: Alert payload dictionary

    Raises:
        ValidationError: If validation fails
    """
    schema = _get_schema("alert_payload.json")
    _validate_against_schema(payload, schema)


def validate_daily_brief(payload: Dict[str, Any]) -> None:
    """
    Validate a daily brief report.

    Args:
        payload: Daily brief dictionary

    Raises:
        ValidationError: If validation fails
    """
    schema = _get_schema("daily_brief.json")
    _validate_against_schema(payload, schema)


def validate_event_analysis(payload: Dict[str, Any]) -> None:
    """
    Validate an event analysis report.

    Args:
        payload: Event analysis dictionary

    Raises:
        ValidationError: If validation fails
    """
    schema = _get_schema("event_analysis.json")
    _validate_against_schema(payload, schema)


def validate_health_response(payload: Dict[str, Any]) -> None:
    """
    Validate a /api/health response.

    Args:
        payload: Health response dictionary

    Raises:
        ValidationError: If validation fails
    """
    schema = _get_schema("health_response.json")
    _validate_against_schema(payload, schema)


# Validator registry for programmatic access
VALIDATORS = {
    "nats_candle": validate_nats_candle,
    "alert": validate_alert,
    "daily_brief": validate_daily_brief,
    "event_analysis": validate_event_analysis,
    "health_response": validate_health_response,
}


def validate(payload: Dict[str, Any], schema_type: str) -> None:
    """
    Validate a payload against a named schema type.

    Args:
        payload: Payload dictionary to validate
        schema_type: One of: nats_candle, alert, daily_brief, event_analysis, health_response

    Raises:
        ValidationError: If validation fails
        ValueError: If schema_type is unknown
    """
    if schema_type not in VALIDATORS:
        raise ValueError(
            f"Unknown schema type: {schema_type}. "
            f"Valid types: {', '.join(VALIDATORS.keys())}"
        )

    validator_func = VALIDATORS[schema_type]
    validator_func(payload)
