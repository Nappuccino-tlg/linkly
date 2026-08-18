import pytest

from app.urlguard import MAX_URL_LENGTH, UnsafeTargetError, check, is_safe

SAFE = [
    "https://example.com/a",
    "http://digikala.com/product/dkp-11827364",
    "https://sub.domain.co.uk/path?q=1#frag",
    "https://8.8.8.8/",
]

UNSAFE = [
    ("javascript:alert(1)", "scheme"),
    ("ftp://example.com/f", "scheme"),
    ("http://localhost:8000/admin", "loopback by name"),
    ("http://api.localhost/x", "loopback subdomain"),
    ("http://127.0.0.1/", "loopback literal"),
    ("http://[::1]/", "ipv6 loopback"),
    ("http://10.0.0.5/internal", "private range"),
    ("http://192.168.1.1/router", "private range"),
    ("http://172.16.0.1/", "private range"),
    ("http://169.254.169.254/latest/meta-data/", "cloud metadata"),
    ("http://printer.local/", "mdns"),
    ("http://vault.internal/secret", "internal suffix"),
    ("http://0.0.0.0/", "unspecified"),
    ("https:///nohost", "no host"),
]


@pytest.mark.parametrize("url", SAFE)
def test_public_urls_are_allowed(url):
    assert is_safe(url)


@pytest.mark.parametrize(("url", "reason"), UNSAFE)
def test_unsafe_urls_are_blocked(url, reason):
    assert not is_safe(url), f"should have been blocked ({reason})"


def test_credentials_in_the_url_are_refused():
    """http://apple.com@evil.example reads as Apple and resolves to evil.example."""
    assert not is_safe("http://apple.com@evil.example/")


def test_trailing_dot_does_not_bypass_the_check():
    assert not is_safe("http://localhost./admin")


def test_uppercase_host_does_not_bypass_the_check():
    assert not is_safe("http://LOCALHOST/admin")


def test_overlong_urls_are_refused():
    assert not is_safe("https://example.com/" + "a" * MAX_URL_LENGTH)


def test_check_explains_why():
    with pytest.raises(UnsafeTargetError, match="private or loopback"):
        check("http://169.254.169.254/")
