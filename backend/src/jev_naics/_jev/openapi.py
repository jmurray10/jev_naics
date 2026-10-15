# Copied from jev_peas 9705080650cb52ba56a337e12eb7bee24f2b3777 packages/jev-common/src/jev_common/openapi.py; keep in sync by hand (CHANGELOG.md).
"""Swagger completeness: every package that serves an API runs this over `app.openapi()`.

A pure check over the OpenAPI dict (no FastAPI dependency). It returns one line per gap
that makes the docs incomplete or hard to navigate; an empty list means complete.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

# Framework-generated schemas: validation errors and multipart bodies (their fields are
# still checked, the schema itself cannot carry a description).
_EXEMPT_SCHEMA = re.compile(r"^(HTTPValidationError|ValidationError|Body_.*)$")
_EXEMPT_ERROR = {"422"}  # FastAPI's own validation response
# FastAPI's default operationId is <function>_<path>_<method>; a chosen id reads better
# in Swagger deep links and generated clients.
_AUTO_OPERATION_ID = re.compile(r"_(get|post|put|patch|delete)$")
_METHODS = {"get", "post", "put", "patch", "delete"}


def _ref_name(schema: Mapping[str, Any]) -> str | None:
    ref = schema.get("$ref", "")
    return ref.rsplit("/", 1)[-1] if ref else None


def _json_schema(body: Mapping[str, Any] | None) -> Mapping[str, Any] | None:
    content = (body or {}).get("content", {})
    media = content.get("application/json") or next(iter(content.values()), None)
    return (media or {}).get("schema")


def _is_typed(schema: Mapping[str, Any] | None) -> bool:
    """A $ref, or an array of $refs; a bare object or a free-form dict is untyped."""
    if not schema:
        return False
    if _ref_name(schema):
        return True
    if schema.get("type") == "array":
        return _is_typed(schema.get("items"))
    return False


def _top_level_names(schema: Mapping[str, Any] | None) -> list[str]:
    if not schema:
        return []
    name = _ref_name(schema)
    if name:
        return [name]
    if schema.get("type") == "array":
        return _top_level_names(schema.get("items"))
    return []


def openapi_problems(spec: Mapping[str, Any]) -> list[str]:
    problems: list[str] = []
    if not str(spec.get("info", {}).get("description", "")).strip():
        problems.append("info.description is empty")

    declared = {}
    for tag in spec.get("tags", []):
        declared[tag["name"]] = tag
        if not str(tag.get("description", "")).strip():
            problems.append(f"tag {tag['name']} has no description")

    schemas: Mapping[str, Any] = spec.get("components", {}).get("schemas", {})
    needs_example: set[str] = set()
    seen_ids: dict[str, str] = {}
    for path, ops in spec.get("paths", {}).items():
        for method, op in ops.items():
            if method not in _METHODS:
                continue
            where = f"{method.upper()} {path}"
            tags = op.get("tags") or []
            if not tags:
                problems.append(f"{where}: no tag")
            for tag in tags:
                if tag not in declared:
                    problems.append(f"{where}: undeclared tag {tag!r}")
            for key in ("summary", "description"):
                if not str(op.get(key, "")).strip():
                    problems.append(f"{where}: no {key}")
            op_id = str(op.get("operationId", ""))
            if not op_id or _AUTO_OPERATION_ID.search(op_id):
                problems.append(f"{where}: operationId {op_id!r} is not chosen")
            elif op_id in seen_ids:
                problems.append(f"{where}: duplicate operationId {op_id!r} (also {seen_ids[op_id]})")
            else:
                seen_ids[op_id] = where

            for param in op.get("parameters", []):
                if not str(param.get("description", "")).strip():
                    problems.append(f"{where}: parameter {param.get('name')} has no description")
            request = _json_schema(op.get("requestBody"))
            needs_example.update(n for n in _top_level_names(request) if not _EXEMPT_SCHEMA.match(n))
            for code, response in op.get("responses", {}).items():
                schema = _json_schema(response)
                if code.startswith("2"):
                    if response.get("description", "").strip() in ("", "Successful Response"):
                        problems.append(f"{where}: {code} response has the generic description")
                    if not _is_typed(schema):
                        problems.append(f"{where}: {code} response is untyped")
                    needs_example.update(_top_level_names(schema))
                elif code not in _EXEMPT_ERROR and not _is_typed(schema):
                    problems.append(f"{where}: {code} response has no typed body")

    for name, schema in schemas.items():
        exempt = bool(_EXEMPT_SCHEMA.match(name))
        if not exempt and not str(schema.get("description", "")).strip():
            problems.append(f"schema {name} has no description")
        if name.startswith(("HTTPValidationError", "ValidationError")):
            continue
        for field, prop in schema.get("properties", {}).items():
            if not str(prop.get("description", "")).strip():
                problems.append(f"field {name}.{field} has no description")
    for name in sorted(needs_example):
        schema = schemas.get(name, {})
        if not (schema.get("examples") or schema.get("example")):
            problems.append(f"schema {name} has no example")
    return problems


# Swagger UI settings for every package: a search box over operations, a URL per
# operation, request timing on "Try it out", operations listed but collapsed, and the
# schema section collapsed so the workflow comes first.
SWAGGER_UI: dict[str, object] = {
    "filter": True,
    "deepLinking": True,
    "displayRequestDuration": True,
    "docExpansion": "list",
    "defaultModelsExpandDepth": 0,
}
