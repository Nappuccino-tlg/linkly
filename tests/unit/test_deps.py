"""client_ip: which address the server is willing to believe.

The value keys the per-IP rate limit and seeds the unique-visitor hash, so "believe the
header" and "believe the socket" are not a style choice.
"""

import pytest
from starlette.requests import Request

from app import deps
from app.deps import client_ip

PEER = "203.0.113.7"


def make_request(forwarded: str | None = None, peer: str | None = PEER) -> Request:
    headers = [(b"x-forwarded-for", forwarded.encode())] if forwarded is not None else []
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": headers,
        "client": (peer, 54321) if peer else None,
    }
    return Request(scope)


@pytest.fixture
def hops(monkeypatch):
    def set_hops(value: int) -> None:
        monkeypatch.setattr(deps.settings, "trusted_proxy_hops", value)

    return set_hops


def test_directly_exposed_ignores_the_header_entirely(hops):
    hops(0)
    assert client_ip(make_request("1.2.3.4")) == PEER


def test_one_trusted_hop_takes_the_entry_that_proxy_appended(hops):
    hops(1)
    assert client_ip(make_request("1.2.3.4")) == "1.2.3.4"


def test_entries_to_the_left_of_the_trusted_hop_are_ignored(hops):
    """The forged half of a header a client controls."""
    hops(1)
    assert client_ip(make_request("9.9.9.9, 1.2.3.4")) == "1.2.3.4"


def test_two_trusted_hops_reach_past_the_inner_proxy(hops):
    hops(2)
    assert client_ip(make_request("1.2.3.4, 10.0.0.1")) == "1.2.3.4"


def test_a_chain_shorter_than_configured_falls_back_to_the_peer(hops):
    """Not the chain we were told to expect, so nothing in it is trustworthy."""
    hops(2)
    assert client_ip(make_request("1.2.3.4")) == PEER


def test_a_value_that_is_not_an_address_falls_back_to_the_peer(hops):
    hops(1)
    assert client_ip(make_request("not-an-ip")) == PEER


def test_a_missing_header_falls_back_to_the_peer(hops):
    hops(1)
    assert client_ip(make_request()) == PEER


def test_an_empty_header_falls_back_to_the_peer(hops):
    hops(1)
    assert client_ip(make_request("   ,  ")) == PEER


def test_ipv6_is_accepted(hops):
    hops(1)
    assert client_ip(make_request("2001:db8::1")) == "2001:db8::1"


def test_no_peer_and_no_header_is_simply_unknown(hops):
    hops(0)
    assert client_ip(make_request(peer=None)) is None
