"""Manifest signatures (spec §12): Ed25519 over the canonical manifest minus
its `signature` member; keys identified by the SHA-256 of their SPKI DER."""

from __future__ import annotations

import base64
import hashlib

from .jcs import canonicalize


def _spki_der(public_key_pem: str) -> bytes:
    body = "".join(line for line in public_key_pem.strip().splitlines() if not line.startswith("-----"))
    return base64.b64decode(body)


def key_id(public_key_pem: str) -> str:
    return hashlib.sha256(_spki_der(public_key_pem)).hexdigest()[:16]


def signing_payload(document: dict) -> bytes:
    unsigned = {k: v for k, v in document.items() if k != "signature"}
    return canonicalize(unsigned).encode("utf-8")


def verify_manifest(document: dict, anchors: list[dict]) -> tuple[bool, str]:
    """Returns (ok, reason). Reasons: 'ok', 'unsigned', 'unknown-key',
    'bad-signature', 'error'. Needs the `cryptography` package."""
    signature = document.get("signature")
    if not signature:
        return False, "unsigned"
    anchor = next((a for a in anchors if a["keyId"] == signature.get("keyId")), None)
    if anchor is None:
        return False, "unknown-key"
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    except ImportError as err:
        raise RuntimeError("signature verification needs the 'cryptography' package") from err
    try:
        if key_id(anchor["publicKeyPem"]) != signature["keyId"]:
            return False, "unknown-key"
        raw = _spki_der(anchor["publicKeyPem"])[-32:]
        Ed25519PublicKey.from_public_bytes(raw).verify(
            base64.b64decode(signature["value"]), signing_payload(document)
        )
        return True, "ok"
    except InvalidSignature:
        return False, "bad-signature"
    except Exception as err:  # noqa: BLE001 - surfaced as a typed verdict
        return False, f"error: {err}"
