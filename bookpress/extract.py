"""Stage 1 - dump per-page line/span geometry from a scanned+OCR PDF into JSON.

The OCR text layer of a scan is usually readable but structurally useless: one
visual line is often split into several spans, reading order is unreliable, and
oversized initials break line boxes.  Keeping the geometry makes every later
stage able to reason about the page instead of the text stream.

Output JSON: {source, page_count, page_rect,
              pages: [{n, w, h, n_images,
                       lines: [{text,x0,y0,x1,y1,size,by0,by1,spans:[...]}]}]}
"""
from __future__ import annotations

import json
import statistics
from pathlib import Path

import pymupdf


def norm_text(s: str) -> str:
    """Normalise the odd code points OCR layers love to produce."""
    return (s.replace("\u00ac", "-")          # not-sign where a hyphen belongs
             .replace("\u2028", " ")
             .replace("\ufb01", "fi")
             .replace("\ufb02", "fl"))


def page_lines(page) -> tuple[list[dict], int]:
    d = page.get_text("dict")
    lines, n_img = [], 0
    for b in d["blocks"]:
        if b["type"] != 0:
            n_img += 1
            continue
        for l in b["lines"]:
            spans = []
            for s in l["spans"]:
                t = norm_text(s["text"])
                if not t:
                    continue
                spans.append({
                    "text": t,
                    "x0": round(s["bbox"][0], 2),
                    "y0": round(s["bbox"][1], 2),
                    "x1": round(s["bbox"][2], 2),
                    "y1": round(s["bbox"][3], 2),
                    "size": round(s["size"], 2),
                    "font": s["font"],
                    "flags": s["flags"],
                })
            if not spans or not "".join(s["text"] for s in spans).strip():
                continue
            lines.append({
                "text": "".join(s["text"] for s in spans),
                "x0": round(l["bbox"][0], 2),
                "y0": round(l["bbox"][1], 2),
                "x1": round(l["bbox"][2], 2),
                "y1": round(l["bbox"][3], 2),
                "size": round(max(s["size"] for s in spans), 2),
                "spans": spans,
            })
    return lines, n_img


def sort_lines(lines: list[dict]) -> None:
    """Sort by the baseline of ordinary-sized spans.  An oversized drop-cap glyph
    makes the line bbox start far above the actual text line, so the baseline of
    the body-sized spans (by0/by1) is what should drive reading order."""
    med = statistics.median([l["size"] for l in lines]) if lines else 12.0
    for l in lines:
        body = [s for s in l["spans"] if s["size"] < 2.5 * med]
        if body:
            l["by0"] = round(min(s["y0"] for s in body), 2)
            l["by1"] = round(max(s["y1"] for s in body), 2)
        else:
            l["by0"], l["by1"] = l["y0"], l["y1"]
    lines.sort(key=lambda l: (round(max((s["y1"] for s in l["spans"]
                                         if s["size"] < 2.5 * med), default=l["y1"]), 1),
                              l["x0"]))


def extract(pdf_path: Path, out_path: Path) -> dict:
    doc = pymupdf.open(str(pdf_path))
    pages = []
    for pno in range(doc.page_count):
        page = doc[pno]
        lines, n_img = page_lines(page)
        sort_lines(lines)
        pages.append({"n": pno,
                      "w": round(page.rect.width, 2),
                      "h": round(page.rect.height, 2),
                      "n_images": n_img,
                      "lines": lines})
    out = {"source": str(pdf_path),
           "page_count": doc.page_count,
           "page_rect": [round(doc[0].rect.width, 2), round(doc[0].rect.height, 2)],
           "pages": pages}
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out), encoding="utf-8")
    print(f"{pdf_path.name}: {len(pages)} pages -> {out_path.name} "
          f"({out_path.stat().st_size / 1e6:.1f} MB)")
    return out
