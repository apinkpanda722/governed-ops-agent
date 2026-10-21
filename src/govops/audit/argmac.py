"""Dedicated argument HMAC key loading and privacy-preserving digests."""

import hashlib
import hmac
import json
import os
import re
from dataclasses import dataclass, field

ENV_NAME = "AUDIT_ARGS_HMAC_KEY"
_KEY_CODES = frozenset(
    {
        "audit_args_hmac_key_missing",
        "audit_args_hmac_key_invalid",
        "audit_args_hmac_key_reused",
    }
)


class ArgsMacKeyError(Exception):
    """Expose only a fixed error code, never key material."""

    def __init__(self, code: str):
        if code not in _KEY_CODES:
            raise ValueError("Invalid argument HMAC key error code")
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class ArgsMac:
    kid: str
    _key: bytes = field(repr=False, compare=False)

    def digest(self, tool_name: str, arguments: object) -> str:
        if isinstance(arguments, str):
            data = "raw\0" + tool_name + "\0" + arguments
            try:
                parsed = json.loads(arguments)
                if isinstance(parsed, dict):
                    data = json.dumps(
                        {"tool": tool_name, "args": parsed},
                        sort_keys=True,
                        separators=(",", ":"),
                        ensure_ascii=False,
                        allow_nan=False,
                    )
            except (ValueError, RecursionError):
                pass
        else:
            data = "raw-nonstr\0" + tool_name
        return hmac.new(self._key, data.encode("utf-8"), hashlib.sha256).hexdigest()


def load_args_mac(*, signing_key_raw: bytes | None = None) -> ArgsMac:
    value = os.environ.get(ENV_NAME, "")
    if not value.strip():
        raise ArgsMacKeyError("audit_args_hmac_key_missing")
    if re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ArgsMacKeyError("audit_args_hmac_key_invalid")
    key = bytes.fromhex(value)
    if signing_key_raw is not None and hmac.compare_digest(key, signing_key_raw):
        raise ArgsMacKeyError("audit_args_hmac_key_reused")
    kid = hashlib.sha256(b"govops-args-hmac\0" + key).hexdigest()[:12]
    return ArgsMac(kid=kid, _key=key)
