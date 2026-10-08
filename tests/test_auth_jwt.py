"""JWT decoding tests — Clerk RS256 (JWKS) and HS256 fallback paths.

Regression coverage for the "Could not parse the provided public key" bug:
CLERK_JWKS_URL lived only in .env (invisible to os.getenv), so RS256 Clerk
tokens fell through to the HS256 fallback, where PyJWT tried to parse the
HMAC secret as an RSA public key.
"""

from __future__ import annotations

from types import SimpleNamespace

import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

import src.infrastructure.auth.jwt as jwt_module
from src.config.settings import AuthSettings
from src.infrastructure.auth.jwt import UserClaims, decode_jwt_token


def _rsa_keypair() -> tuple[bytes, bytes]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    public_pem = key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return private_pem, public_pem


def test_rs256_token_without_jwks_gives_clear_error(monkeypatch) -> None:
    """RS256 token must not surface 'Could not parse the provided public key'."""
    private_pem, _ = _rsa_keypair()
    token = pyjwt.encode(
        {"sub": "user_clerk", "email": "a@b.c"}, private_pem, algorithm="RS256"
    )

    monkeypatch.setattr(jwt_module, "get_jwks_client", lambda: None)
    with pytest.raises(ValueError) as exc_info:
        decode_jwt_token(token)

    message = str(exc_info.value)
    assert "Could not parse the provided public key" not in message
    assert "RS256" in message
    assert "JWKS" in message


def test_rs256_token_verified_via_jwks_client(monkeypatch) -> None:
    """When a JWKS client is available, RS256 tokens verify and yield claims."""
    private_pem, public_pem = _rsa_keypair()
    token = pyjwt.encode(
        {"sub": "user_clerk", "email": "clerk@example.com"},
        private_pem,
        algorithm="RS256",
        headers={"kid": "test-key-1"},
    )

    fake_client = SimpleNamespace(
        get_signing_key_from_jwt=lambda _tok: SimpleNamespace(key=public_pem)
    )
    monkeypatch.setattr(jwt_module, "get_jwks_client", lambda: fake_client)

    claims = decode_jwt_token(token)
    assert isinstance(claims, UserClaims)
    assert claims.user_id == "user_clerk"
    assert claims.email == "clerk@example.com"


def test_hs256_token_still_decodes_with_shared_secret() -> None:
    from src.config.settings import settings

    token = pyjwt.encode(
        {"sub": "user_local", "email": "local@example.com"},
        settings.auth.supabase_jwt_secret,
        algorithm="HS256",
    )
    claims = decode_jwt_token(token)
    assert claims.user_id == "user_local"


def test_hs256_bad_signature_rejected() -> None:
    token = pyjwt.encode({"sub": "user_x"}, "wrong-secret-value-xxxxxxxxxxxx", algorithm="HS256")
    with pytest.raises(ValueError) as exc_info:
        decode_jwt_token(token)
    assert "Invalid token" in str(exc_info.value)


def test_clerk_jwks_url_loaded_from_environment(monkeypatch) -> None:
    """The JWKS URL must be reachable through settings (pydantic env parsing),
    not only os.getenv — .env values never reach os.environ."""
    monkeypatch.setenv("CLERK_JWKS_URL", "https://example.com/.well-known/jwks.json")
    auth_settings = AuthSettings()
    assert auth_settings.clerk_jwks_url == "https://example.com/.well-known/jwks.json"
