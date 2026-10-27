"""Find and mask Aadhaar numbers before anything leaves the machine.

Images go through a local OCR (RapidOCR, declared in models.yaml) so the Meta API only ever
sees the masked copy. Masking follows UIDAI's masked-Aadhaar format: the first 8 digits are
hidden, the last 4 stay visible. Candidates must pass the Verhoeff checksum and cannot start
with 0 or 1, which keeps phone and most account numbers from being masked by mistake.
"""

import io
import re
from dataclasses import dataclass
from functools import lru_cache

from PIL import Image, ImageDraw

# Verhoeff tables
_D = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9], [1, 2, 3, 4, 0, 6, 7, 8, 9, 5],
    [2, 3, 4, 0, 1, 7, 8, 9, 5, 6], [3, 4, 0, 1, 2, 8, 9, 5, 6, 7],
    [4, 0, 1, 2, 3, 9, 5, 6, 7, 8], [5, 9, 8, 7, 6, 0, 4, 3, 2, 1],
    [6, 5, 9, 8, 7, 1, 0, 4, 3, 2], [7, 6, 5, 9, 8, 2, 1, 0, 4, 3],
    [8, 7, 6, 5, 9, 3, 2, 1, 0, 4], [9, 8, 7, 6, 5, 4, 3, 2, 1, 0],
]  # fmt: skip
_P = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9], [1, 5, 7, 6, 2, 8, 3, 0, 9, 4],
    [5, 8, 0, 3, 7, 9, 6, 1, 4, 2], [8, 9, 1, 6, 0, 4, 3, 5, 2, 7],
    [9, 4, 5, 3, 1, 2, 6, 8, 7, 0], [4, 2, 8, 6, 5, 7, 3, 9, 0, 1],
    [2, 7, 9, 3, 8, 0, 6, 4, 1, 5], [7, 0, 4, 6, 9, 1, 3, 2, 5, 8],
]  # fmt: skip
_INV = [0, 4, 3, 2, 1, 5, 6, 7, 8, 9]


def verhoeff_valid(digits: str) -> bool:
    c = 0
    for i, ch in enumerate(reversed(digits)):
        c = _D[c][_P[i % 8][int(ch)]]
    return c == 0


def verhoeff_check_digit(digits: str) -> str:
    c = 0
    for i, ch in enumerate(reversed(digits)):
        c = _D[c][_P[(i + 1) % 8][int(ch)]]
    return str(_INV[c])


def is_aadhaar(digits: str) -> bool:
    return len(digits) == 12 and digits[0] not in "01" and verhoeff_valid(digits)


_TEXT_RE = re.compile(r"(?<!\d)(\d{4})[ -]?(\d{4})[ -]?(\d{4})(?!\d)")


def mask_text(text: str) -> tuple[str, int]:
    """Mask Aadhaar numbers in free text. Returns the new text and how many were masked."""
    count = 0

    def sub(m: re.Match) -> str:
        nonlocal count
        if not is_aadhaar("".join(m.groups())):
            return m.group(0)
        count += 1
        return f"XXXX XXXX {m.group(3)}"

    return _TEXT_RE.sub(sub, text), count


@dataclass
class Box:
    x0: float
    y0: float
    x1: float
    y1: float
    text: str


@lru_cache
def _ocr():
    from rapidocr_onnxruntime import RapidOCR  # loads ONNX models; ~1s, so cached

    return RapidOCR()


def ocr_boxes(image: Image.Image) -> list[Box]:
    import numpy as np

    result, _ = _ocr()(np.array(image.convert("RGB")))
    boxes = []
    for quad, txt, _score in result or []:
        xs = [p[0] for p in quad]
        ys = [p[1] for p in quad]
        boxes.append(Box(min(xs), min(ys), max(xs), max(ys), txt))
    return boxes


def _regions_to_mask(boxes: list[Box]) -> list[tuple[float, float, float, float]]:
    """Rectangles covering the first 8 digits of every Aadhaar number found."""
    regions = []
    # Case 1: one OCR box holds the whole number; mask the part before the last 4 digits.
    for b in boxes:
        for m in _TEXT_RE.finditer(b.text):
            if is_aadhaar("".join(m.groups())):
                n = max(len(b.text), 1)
                width = b.x1 - b.x0
                start = b.x0 + width * m.start(1) / n
                end = b.x0 + width * m.start(3) / n
                regions.append((start, b.y0, end, b.y1))
    # Case 2: OCR split it into three 4-digit boxes on one line.
    quads = sorted(
        (b for b in boxes if re.fullmatch(r"\d{4}", b.text.strip())), key=lambda b: (b.y0, b.x0)
    )
    for a, b, c in zip(quads, quads[1:], quads[2:], strict=False):
        same_line = max(abs(a.y0 - b.y0), abs(b.y0 - c.y0)) < (a.y1 - a.y0) * 0.6
        if same_line and a.x1 <= b.x0 <= c.x0 and is_aadhaar(a.text + b.text + c.text):
            regions.append((a.x0, min(a.y0, b.y0), b.x1, max(a.y1, b.y1)))
    return regions


def mask_image(data: bytes) -> tuple[bytes, int]:
    """Return a PNG with every Aadhaar number's first 8 digits blacked out, and the count."""
    image = Image.open(io.BytesIO(data))
    image.load()
    image = image.convert("RGB")
    regions = _regions_to_mask(ocr_boxes(image))
    draw = ImageDraw.Draw(image)
    pad = 4
    for x0, y0, x1, y1 in regions:
        draw.rectangle((x0 - pad, y0 - pad, x1 + pad, y1 + pad), fill="black")
    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue(), len(regions)
