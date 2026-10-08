"""Canonical audit hashes and Ed25519 signing without sensitive error details."""

import hashlib
import json
import math
import re
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from pathlib import Path

from cryptography.exceptions import InvalidSignature, UnsupportedAlgorithm
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    PublicFormat,
    load_pem_private_key,
    load_pem_public_key,
)

from govops.env import env_or

DEFAULT_KEY_FILE = "data/interim/audit_ed25519.pem"
DEFAULT_PUBLIC_KEY_FILE = "data/seed/audit_public_key.pem"
_KEY_CODES = frozenset(
    {
        "audit_key_missing",
        "audit_key_permissions",
        "audit_key_invalid",
        "audit_key_mismatch",
        "audit_public_key_missing",
        "audit_public_key_invalid",
    }
)


class AuditKeyError(Exception):
    """Expose a fixed code only, never the underlying key-loading exception."""

    def __init__(self, code: str):
        if code not in _KEY_CODES:
            raise ValueError("Invalid audit key error code")
        self.code = code
        super().__init__(code)


def _public_der(public: Ed25519PublicKey) -> bytes:
    return public.public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)


def kid_of(public: Ed25519PublicKey) -> str:
    return hashlib.sha256(_public_der(public)).hexdigest()[:12]


@dataclass(frozen=True)
class SigningKey:
    private: Ed25519PrivateKey
    public: Ed25519PublicKey
    kid: str

    def sign(self, hash_hex: str) -> str:
        return self.private.sign(hash_hex.encode("ascii")).hex()


def load_public_keys(
    public_key_file: Path | None = None,
) -> dict[str, Ed25519PublicKey]:
    path = (
        Path(public_key_file)
        if public_key_file is not None
        else Path(env_or("AUDIT_PUBLIC_KEY_FILE", "data/seed/audit_public_key.pem"))
    )
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        raise AuditKeyError("audit_public_key_missing") from None
    except OSError:
        raise AuditKeyError("audit_public_key_invalid") from None
    try:
        public = load_pem_public_key(data)
        if not isinstance(public, Ed25519PublicKey):
            raise TypeError
    except (ValueError, TypeError, UnsupportedAlgorithm):
        raise AuditKeyError("audit_public_key_invalid") from None
    return {kid_of(public): public}


def load_signing_key(
    key_file: Path | None = None, public_key_file: Path | None = None
) -> SigningKey:
    path = (
        Path(key_file)
        if key_file is not None
        else Path(env_or("AUDIT_KEY_FILE", "data/interim/audit_ed25519.pem"))
    )
    try:
        mode = path.stat().st_mode & 0o777
    except FileNotFoundError:
        raise AuditKeyError("audit_key_missing") from None
    except OSError:
        raise AuditKeyError("audit_key_permissions") from None
    if mode != 0o600:
        raise AuditKeyError("audit_key_permissions")
    try:
        data = path.read_bytes()
        if not data.startswith(b"-----BEGIN PRIVATE KEY-----"):
            raise ValueError
        private = load_pem_private_key(data, password=None)
        if not isinstance(private, Ed25519PrivateKey):
            raise TypeError
    except (OSError, ValueError, TypeError, UnsupportedAlgorithm):
        raise AuditKeyError("audit_key_invalid") from None
    public = private.public_key()
    committed = next(iter(load_public_keys(public_key_file).values()))
    if _public_der(public) != _public_der(committed):
        raise AuditKeyError("audit_key_mismatch")
    return SigningKey(private, public, kid_of(public))


def verify_hash(public: Ed25519PublicKey, hash_hex: str, sig_hex: str) -> bool:
    if (
        not isinstance(hash_hex, str)
        or not isinstance(sig_hex, str)
        or re.fullmatch(r"[0-9a-fA-F]{64}", hash_hex) is None
        or re.fullmatch(r"[0-9a-fA-F]{128}", sig_hex) is None
    ):
        return False
    try:
        public.verify(bytes.fromhex(sig_hex), hash_hex.encode("ascii"))
    except (InvalidSignature, ValueError, TypeError):
        return False
    return True


def _has_float(value) -> bool:
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("Nonfinite audit number")
        return True
    children = ()
    if isinstance(value, dict):
        children = (*value.keys(), *value.values())
    elif isinstance(value, (list, tuple)):
        children = value
    found = False
    for child in children:
        found = _has_float(child) or found
    return found


def canonical_json(value) -> str:
    if _has_float(value):
        raise TypeError("Audit floats must be formatted as strings")
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def record_hash(record: dict) -> str:
    if "hash" in record or "sig" in record:
        raise ValueError("Reserved audit hash fields")
    return hashlib.sha256(canonical_json(record).encode("utf-8")).hexdigest()


def format_usd(amount: float | Decimal | int) -> str:  # noqa: PYI041
    if isinstance(amount, bool) or not isinstance(amount, (float, Decimal, int)):
        raise ValueError("Invalid audit amount")  # noqa: TRY004 - contract rejects bool
    value = Decimal(str(amount))
    if not value.is_finite() or value < 0:
        raise ValueError("Invalid audit amount")
    with localcontext() as context:
        context.prec = max(28, len(value.as_tuple().digits), value.adjusted() + 7)
        rounded = value.quantize(Decimal("0.000001"), rounding=ROUND_HALF_EVEN)
    return format(rounded.copy_abs(), ".6f")
