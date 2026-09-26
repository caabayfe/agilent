from typing import NoReturn

import jwt
import pytest

from cfa.identity.tokens import TokenValidator


def _validator(monkeypatch: pytest.MonkeyPatch, error: Exception) -> TokenValidator:
    validator = TokenValidator("http://idp.invalid/jwks", "issuer")

    def fail(_token: str | bytes) -> NoReturn:
        raise error

    monkeypatch.setattr(validator._jwks, "get_signing_key_from_jwt", fail)
    return validator


async def test_token_signed_by_a_rotated_key_is_invalid_not_a_server_error(monkeypatch: pytest.MonkeyPatch) -> None:
    validator = _validator(monkeypatch, jwt.PyJWKClientError('Unable to find a signing key that matches: "kid"'))
    with pytest.raises(jwt.InvalidTokenError):
        await validator.decode("token", "aud")


async def test_unreachable_idp_is_not_reported_as_an_invalid_token(monkeypatch: pytest.MonkeyPatch) -> None:
    validator = _validator(monkeypatch, jwt.PyJWKClientConnectionError("down"))
    with pytest.raises(jwt.PyJWKClientConnectionError) as info:
        await validator.decode("token", "aud")
    assert not isinstance(info.value, jwt.InvalidTokenError)
