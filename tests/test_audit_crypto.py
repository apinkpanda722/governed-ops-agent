"""Audit cryptographic primitives use real temporary keys, never production keys."""

import hashlib
import random
import re
from decimal import Decimal

import pytest
from audit_keys import make_key_pair
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)

from govops.audit.crypto import (
    AuditKeyError,
    canonical_json,
    format_usd,
    kid_of,
    load_public_keys,
    load_signing_key,
    record_hash,
    verify_hash,
)


def expect_error(code, *args):
    with pytest.raises(AuditKeyError) as caught:
        load_signing_key(*args)
    assert str(caught.value) == code
    assert caught.value.code == code
    assert caught.value.args == (code,)
    assert caught.value.__cause__ is None


def test_signing_and_public_only(audit_key_pair):
    key = load_signing_key()
    assert re.fullmatch(r"[0-9a-f]{12}", key.kid)
    assert key.kid == audit_key_pair.kid == kid_of(key.public)
    digest = record_hash({"kid": key.kid, "event": "합성"})
    signature = key.sign(digest)
    assert re.fullmatch(r"[0-9a-f]{128}", signature)
    assert signature == key.sign(digest)
    assert verify_hash(key.public, digest, signature)
    assert not verify_hash(key.public, "0" * 64, signature)
    changed = ("0" if signature[0] != "0" else "1") + signature[1:]
    assert not verify_hash(key.public, digest, changed)
    keys = load_public_keys()
    assert list(keys) == [key.kid]
    assert verify_hash(keys[key.kid], digest, signature)


@pytest.mark.parametrize("sig", ["", "zz", "00", None, 12])
def test_signature_format_errors(sig):
    assert not verify_hash(load_signing_key().public, "a" * 64, sig)


@pytest.mark.parametrize("digest", [None, 12, "한글"])
def test_hash_format_errors(digest):
    assert not verify_hash(load_signing_key().public, digest, "00" * 64)


@pytest.mark.parametrize("mode", [0o644, 0o640, 0o700])
def test_permissions(tmp_path, mode):
    pair = make_key_pair(tmp_path)
    pair.private_path.chmod(mode)
    expect_error("audit_key_permissions", pair.private_path, pair.public_path)


def test_missing_and_mismatched(tmp_path):
    pair = make_key_pair(tmp_path / "one")
    other = make_key_pair(tmp_path / "two")
    expect_error("audit_key_missing", tmp_path / "absent", pair.public_path)
    expect_error("audit_public_key_missing", pair.private_path, tmp_path / "absent")
    expect_error("audit_key_mismatch", pair.private_path, other.public_path)


@pytest.mark.parametrize("kind", ["text", "rsa", "public"])
def test_invalid_private_key(tmp_path, kind):
    pair = make_key_pair(tmp_path)
    if kind == "text":
        value = b"not a key"
    elif kind == "public":
        value = pair.public_path.read_bytes()
    else:
        value = rsa.generate_private_key(
            public_exponent=65537, key_size=2048
        ).private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())
    pair.private_path.write_bytes(value)
    expect_error("audit_key_invalid", pair.private_path, pair.public_path)


@pytest.mark.parametrize("kind", ["text", "rsa", "private"])
def test_invalid_public_key(tmp_path, kind):
    pair = make_key_pair(tmp_path)
    if kind == "text":
        value = b"not a key"
    elif kind == "private":
        value = pair.private_path.read_bytes()
    else:
        value = (
            rsa.generate_private_key(public_exponent=65537, key_size=2048)
            .public_key()
            .public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo)
        )
    pair.public_path.write_bytes(value)
    expect_error("audit_public_key_invalid", pair.private_path, pair.public_path)
    with pytest.raises(AuditKeyError, match="^audit_public_key_invalid$"):
        load_public_keys(pair.public_path)


def test_public_keys_without_private(tmp_path):
    pair = make_key_pair(tmp_path)
    pair.private_path.unlink()
    assert list(load_public_keys(pair.public_path)) == [pair.kid]
    pair.public_path.unlink()
    with pytest.raises(AuditKeyError, match="^audit_public_key_missing$"):
        load_public_keys(pair.public_path)


def test_environment_missing(monkeypatch, tmp_path):
    monkeypatch.setenv("AUDIT_KEY_FILE", str(tmp_path / "absent"))
    expect_error("audit_key_missing")


@pytest.mark.parametrize("value", [None, "", " \t"])
def test_environment_default(monkeypatch, tmp_path, value):
    monkeypatch.chdir(tmp_path)
    if value is None:
        monkeypatch.delenv("AUDIT_KEY_FILE")
    else:
        monkeypatch.setenv("AUDIT_KEY_FILE", value)
    expect_error("audit_key_missing")


def test_canonical_and_hash():
    value = {"z": [True, None, {"한글": "보존"}], "a": 1}
    expected = '{"a":1,"z":[true,null,{"한글":"보존"}]}'
    assert canonical_json(value) == expected == canonical_json(value)
    assert canonical_json(dict(reversed(list(value.items())))) == expected
    assert record_hash(value) == hashlib.sha256(expected.encode("utf-8")).hexdigest()


@pytest.mark.parametrize("value", [1.0, {"x": [1.2]}, {1.2: "key"}, (1.2,)])
def test_float_rejected(value):
    with pytest.raises(TypeError):
        canonical_json(value)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_nonfinite_rejected(value):
    with pytest.raises(ValueError):
        canonical_json({"nested": [value]})


@pytest.mark.parametrize("field", ["hash", "sig"])
def test_hash_rejects_reserved_fields(field):
    with pytest.raises(ValueError):
        record_hash({field: "anything"})


@pytest.mark.parametrize(
    "value,expected",
    [
        (0, "0.000000"),
        (1.23456789, "1.234568"),
        (Decimal("0.0000005"), "0.000000"),
        (Decimal("0.0000015"), "0.000002"),
    ],
)
def test_usd_rounding(value, expected):
    assert format_usd(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        -1,
        -0.1,
        Decimal("-0.0000001"),
        float("nan"),
        float("inf"),
        float("-inf"),
        Decimal("NaN"),
        Decimal("Infinity"),
        True,
        False,
    ],
)
def test_invalid_usd(value):
    with pytest.raises(ValueError):
        format_usd(value)


def test_usd_seeded_format():
    rng = random.Random(1234)
    for _ in range(1000):
        assert re.fullmatch(r"\d+\.\d{6}", format_usd(rng.uniform(0, 10000)))
