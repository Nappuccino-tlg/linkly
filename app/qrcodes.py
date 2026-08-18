import io
from typing import Literal

import qrcode
import qrcode.image.svg

QrFormat = Literal["png", "svg"]

MEDIA_TYPES: dict[QrFormat, str] = {"png": "image/png", "svg": "image/svg+xml"}

MIN_BOX_SIZE = 2
MAX_BOX_SIZE = 40


def render(data: str, fmt: QrFormat = "png", box_size: int = 10) -> bytes:
    """Encode `data` as a QR code.

    SVG is generated through the path factory, which needs no Pillow and scales to any
    print size -- worth having for anyone putting a short link on a poster.
    """
    factory = qrcode.image.svg.SvgPathImage if fmt == "svg" else None
    image = qrcode.make(data, image_factory=factory, box_size=box_size)

    buffer = io.BytesIO()
    image.save(buffer)
    return buffer.getvalue()
