"""Tests for GitHub App JWT generation.

A real RSA key is generated per run rather than checked in, so the
signature is genuinely verified rather than merely produced.
"""

from datetime import datetime, timezone

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa

from gatox.github.app_auth import GitHubAppAuth
from gatox.github.credentials import AppAuthError, AppJwtProvider


def _pem(key) -> str:
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()


@pytest.fixture(scope="module")
def rsa_keypair():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_pem = (
        key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode()
    )
    return _pem(key), public_pem


def test_jwt_verifies_and_carries_expected_claims(rsa_keypair):
    private_pem, public_pem = rsa_keypair
    auth = GitHubAppAuth("12345", private_pem)

    token = auth.generate_jwt()
    claims = jwt.decode(token, public_pem, algorithms=["RS256"], issuer="12345")

    now = int(datetime.now(timezone.utc).timestamp())
    assert claims["iss"] == "12345"
    # iat is backdated to tolerate clock skew between us and GitHub.
    assert claims["iat"] <= now - 55
    assert claims["exp"] - claims["iat"] <= 10 * 60 + 60
    assert jwt.get_unverified_header(token)["alg"] == "RS256"


def test_integer_app_id_is_stringified(rsa_keypair):
    private_pem, public_pem = rsa_keypair
    token = GitHubAppAuth(12345, private_pem).generate_jwt()
    claims = jwt.decode(token, public_pem, algorithms=["RS256"], issuer="12345")
    assert claims["iss"] == "12345"


def test_inline_pem_does_not_set_a_path(rsa_keypair):
    private_pem, _ = rsa_keypair
    auth = GitHubAppAuth("1", private_pem)
    assert auth.private_key_path is None


def test_key_is_read_from_a_file_path(tmp_path, rsa_keypair):
    private_pem, public_pem = rsa_keypair
    key_file = tmp_path / "app.pem"
    key_file.write_text(private_pem)

    auth = GitHubAppAuth("999", str(key_file))
    token = auth.generate_jwt()

    claims = jwt.decode(token, public_pem, algorithms=["RS256"], issuer="999")
    assert claims["iss"] == "999"
    assert str(auth.private_key_path) == str(key_file)


def test_key_file_is_read_only_once(tmp_path, rsa_keypair):
    private_pem, _ = rsa_keypair
    key_file = tmp_path / "app.pem"
    key_file.write_text(private_pem)

    auth = GitHubAppAuth("1", str(key_file))
    auth.generate_jwt()
    key_file.unlink()

    # The cached key keeps working after the file is gone.
    assert auth.generate_jwt()


def test_expiration_above_ten_minutes_is_rejected(rsa_keypair):
    private_pem, _ = rsa_keypair
    with pytest.raises(ValueError, match="10 minutes"):
        GitHubAppAuth("1", private_pem).generate_jwt(expiration_minutes=11)


def test_custom_expiration_is_honoured(rsa_keypair):
    private_pem, public_pem = rsa_keypair
    token = GitHubAppAuth("1", private_pem).generate_jwt(expiration_minutes=5)
    claims = jwt.decode(token, public_pem, algorithms=["RS256"], issuer="1")
    assert claims["exp"] - claims["iat"] <= 5 * 60 + 60


def test_missing_key_file_names_the_path(tmp_path):
    missing = tmp_path / "nope.pem"
    auth = GitHubAppAuth("1", str(missing))

    with pytest.raises(AppAuthError, match=str(missing)):
        auth.generate_jwt()
    # Still a FileNotFoundError for callers that catch the builtin.
    with pytest.raises(FileNotFoundError):
        auth.generate_jwt()


def test_file_that_is_not_pem_is_rejected(tmp_path):
    key_file = tmp_path / "notes.txt"
    key_file.write_text("this is not a key")

    with pytest.raises(AppAuthError, match="PEM"):
        GitHubAppAuth("1", str(key_file)).generate_jwt()


def test_encrypted_key_tells_operator_to_decrypt(tmp_path):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    encrypted = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.BestAvailableEncryption(b"hunter2"),
    ).decode()
    key_file = tmp_path / "enc.pem"
    key_file.write_text(encrypted)

    with pytest.raises(AppAuthError, match="encrypted"):
        GitHubAppAuth("1", str(key_file)).generate_jwt()


def test_non_rsa_key_is_rejected_with_a_clear_message():
    ec_pem = _pem(ec.generate_private_key(ec.SECP256R1()))

    with pytest.raises(AppAuthError, match="RSA"):
        GitHubAppAuth("1", ec_pem).generate_jwt()


def test_garbage_key_is_rejected():
    garbage = "-----BEGIN RSA PRIVATE KEY-----\nnope\n-----END RSA PRIVATE KEY-----"

    with pytest.raises(AppAuthError):
        GitHubAppAuth("1", garbage).generate_jwt()


async def test_jwt_provider_caches_then_remints(rsa_keypair):
    private_pem, _ = rsa_keypair
    provider = AppJwtProvider(GitHubAppAuth("12345", private_pem))

    first = await provider.get_token()
    assert await provider.get_token() == first
    assert provider.is_refreshable is True
    assert "12345" in provider.describe()


async def test_jwt_provider_expiry_is_within_ten_minutes(rsa_keypair):
    private_pem, _ = rsa_keypair
    provider = AppJwtProvider(GitHubAppAuth("12345", private_pem))

    await provider.get_token()
    remaining = provider.expires_at - datetime.now(timezone.utc)
    assert remaining.total_seconds() <= 10 * 60
