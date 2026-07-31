import pytest
from app.services.errors import ServiceError
from app.services.oauth_state import OAuthStateCodec


def test_oauth_state_roundtrip_and_tamper() -> None:
    codec = OAuthStateCodec("secret")
    state = codec.encode(123)
    assert codec.decode(state) == 123
    with pytest.raises(ServiceError):
        codec.decode(state[:-2] + "xx")
