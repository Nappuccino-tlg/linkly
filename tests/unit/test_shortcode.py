from app.shortcode import ALPHABET, DEFAULT_LENGTH, RESERVED_CODES, generate_code


def test_generated_codes_have_the_expected_shape():
    code = generate_code()
    assert len(code) == DEFAULT_LENGTH
    assert set(code) <= set(ALPHABET)


def test_generated_codes_are_url_safe():
    assert all(c.isalnum() for c in ALPHABET)


def test_collisions_are_rare_enough_to_rely_on_retry():
    """Not a proof -- just a guard against a broken generator returning constants."""
    codes = {generate_code() for _ in range(5000)}
    assert len(codes) == 5000


def test_router_prefixes_are_reserved():
    for path in ("api", "auth", "docs", "healthz"):
        assert path in RESERVED_CODES
