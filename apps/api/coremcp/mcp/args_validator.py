from __future__ import annotations

from typing import Any

from jsonschema import SchemaError, ValidationError
from jsonschema.validators import validator_for


def validate_tool_arguments(schema: Any, arguments: Any) -> str | None:
    if not isinstance(schema, dict):
        return None
    try:
        validator_cls = validator_for(schema)
        validator_cls.check_schema(schema)
        validator = validator_cls(schema)
        error = next(validator.iter_errors(arguments), None)
    except SchemaError:
        # Downstream supplied an invalid schema. Do not block the call on a
        # malformed catalog entry; validation/service refresh will surface it.
        return None
    except ValidationError as exc:
        error = exc
    if error is None:
        return None
    path = ".".join(str(part) for part in error.absolute_path)
    prefix = f"{path}: " if path else ""
    return f"{prefix}{error.message}"
