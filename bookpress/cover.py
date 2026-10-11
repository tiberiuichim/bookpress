"""Cut the front cover out of the scan so the EPUB gets one."""
from __future__ import annotations

from pathlib import Path

import pymupdf
from PIL import Image, ImageChops


def cover(book, page: int | None = None, max_width: int = 1400) -> Path | None:
    n = (page if page is not None else book.cfg.get("cover_page", 1))
    doc = pymupdf.open(book.cfg["pdf"])
    if n - 1 >= doc.page_count:
        print(f"!! page {n} is outside the scan")
        return None
    pix = doc[n - 1].get_pixmap(matrix=pymupdf.Matrix(150 / 72, 150 / 72))
    im = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    bg = Image.new("RGB", im.size, (245, 245, 245))
    bbox = ImageChops.difference(im, bg).getbbox()      # trim the scanner border
    if bbox:
        im = im.crop(bbox)
    if im.width > max_width:
        im = im.resize((max_width, int(im.height * max_width / im.width)), Image.LANCZOS)
    book.epub_dir.mkdir(parents=True, exist_ok=True)
    out = book.epub_dir / "cover.jpg"
    im.save(out, quality=88)
    print("wrote", out, im.size)
    return out
