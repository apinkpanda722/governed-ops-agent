"""Canonical idempotency keys and metadata-only effect markers."""

import hashlib
import json
import unicodedata
from collections.abc import Iterable, Mapping
from typing import Any

from govops.tools.contract import IDENTITY_FIELD_NAMES


def _normalize(value: Any) -> Any:
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    if isinstance(value, Mapping):
        result = {}
        for key, child in value.items():
            if not isinstance(key, str):
                raise TypeError("Argument keys must be strings")
            key = _normalize(key)
            if key.lower().replace("_", "").replace("-", "") in IDENTITY_FIELD_NAMES:
                raise ValueError("Identity fields are not tool arguments")
            if key in result:
                raise ValueError("Duplicate normalized argument key")
            result[key] = _normalize(child)
        return result
    if isinstance(value, (list, tuple)):
        return [_normalize(child) for child in value]
    return value


def idempotency_key(task_id: str, tool: str, args: Mapping[str, Any]) -> str:
    canonical = json.dumps(
        {"task_id": task_id, "tool": tool, "args": _normalize(args)},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def make_marker(task_id: str, idem_key: str, approval_id: str) -> dict[str, str]:
    return {
        "govops_idem": idem_key,
        "govops_task": task_id,
        "govops_approval": approval_id,
    }


def has_marker(items: Iterable[Mapping], idem_key: str) -> bool:
    return any(
        isinstance(metadata := item.get("metadata"), Mapping)
        and metadata.get("govops_idem") == idem_key
        for item in items
    )
