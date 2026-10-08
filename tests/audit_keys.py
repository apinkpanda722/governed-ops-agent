"""Real ephemeral signing keys for offline audit tests."""

import hashlib
from dataclasses import dataclass
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)


@dataclass(frozen=True)
class KeyPair:
    private_path: Path
    public_path: Path
    kid: str


def make_key_pair(directory: Path) -> KeyPair:
    directory.mkdir(parents=True, exist_ok=True)
    private = Ed25519PrivateKey.generate()
    public = private.public_key()
    private_path = directory / "private.pem"
    public_path = directory / "public.pem"
    private_path.touch(mode=0o600)
    private_path.chmod(0o600)
    private_path.write_bytes(
        private.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())
    )
    public_path.write_bytes(
        public.public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo)
    )
    public_path.chmod(0o644)
    der = public.public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
    return KeyPair(private_path, public_path, hashlib.sha256(der).hexdigest()[:12])
