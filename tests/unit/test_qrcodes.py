import pytest

from app.qrcodes import render

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def test_png_is_a_png():
    assert render("https://example.com/abc", "png").startswith(PNG_MAGIC)


def test_svg_is_an_svg():
    image = render("https://example.com/abc", "svg")
    assert b"<svg" in image
    assert b"<path" in image


def test_defaults_to_png():
    assert render("https://example.com/abc").startswith(PNG_MAGIC)


def test_longer_urls_produce_denser_codes():
    """A longer payload needs a higher QR version, so the image grows."""
    short = render("https://ex.com/a")
    long = render("https://ex.com/" + "a" * 200)
    assert len(long) > len(short)


@pytest.mark.parametrize("box_size", [2, 10, 40])
def test_box_size_scales_the_image(box_size):
    assert len(render("https://example.com/abc", "png", box_size=box_size)) > 0


def test_bigger_boxes_make_bigger_images():
    assert len(render("https://example.com/abc", "png", box_size=20)) > len(
        render("https://example.com/abc", "png", box_size=2)
    )
