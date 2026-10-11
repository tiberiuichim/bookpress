"""Stage 3 - recover the oversized chapter initial (drop cap).

A scan's OCR usually mangles or drops the big first letter of a chapter, so the
text starts mid-word ("ho had denounced ...").  Two ways to fix it:

  ocr   - crop the drop-cap box from the page image, find the ink bounding box,
          OCR one glyph at two resolutions / three page-segmentation modes, and
          accept the letter if it turns the fragment into a word that this book
          really uses at the start of a sentence.
  manual - overrides/dropcaps.json, applied first:
          [{"page": 18, "old": "ho had", "new": "Who had"}]

Requires the ``tesseract`` binary; without it only manual overrides work.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from collections import Counter
from pathlib import Path

import pymupdf
from PIL import Image

from .textutil import WORD_RE, read_words

DATELINE_RE = re.compile(
    r"^[A-ZÀ-Þ][\w'’.\- ]{1,28}?,?\s+(\d{1,2}\s+)?[A-ZÀ-Þ][a-zà-ÿ]+\.?\s+\d{3,4}$")


def tesseract_available() -> bool:
    return shutil.which("tesseract") is not None


def ocr_glyph(img, psm: int) -> str:
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        tmp = f.name
    try:
        img.save(tmp, format="PNG")
        r = subprocess.run(
            ["tesseract", tmp, "stdout", "--psm", str(psm),
             "-c", "tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZ"],
            capture_output=True, text=True)
        return r.stdout.strip()
    finally:
        Path(tmp).unlink(missing_ok=True)


def _largest_run(profile, min_gap: int = 2):
    """Index range of the contiguous run carrying the most ink."""
    runs, cur, cur_start = [], 0, None
    for i, v in enumerate(profile):
        if v > 0:
            if cur_start is None:
                cur_start, cur = i, 0
            cur += v
        else:
            if cur_start is not None:
                runs.append((cur_start, i - 1, cur))
            cur_start = None
    if cur_start is not None:
        runs.append((cur_start, len(profile) - 1, cur))
    if not runs:
        return None
    merged = [runs[0]]
    for s, e, t in runs[1:]:
        ps, pe, pt = merged[-1]
        if s - pe <= min_gap:                 # broken serif / separate stroke of i
            merged[-1] = (ps, e, pt + t)
        else:
            merged.append((s, e, t))
    return max(merged, key=lambda r: r[2])[:2]


def crop_glyph(page, rect, dpi: int = 400):
    import numpy as np
    pix = page.get_pixmap(matrix=pymupdf.Matrix(dpi / 72, dpi / 72), clip=rect,
                         colorspace=pymupdf.csGRAY)
    a = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width)
    dark = a < 150
    if not dark.any():
        return None
    rows = _largest_run(dark.sum(axis=1))
    if not rows:
        return None
    band = dark[rows[0]:rows[1] + 1]
    cols = _largest_run(band.sum(axis=0))
    if not cols:
        return None
    pad = 5
    y0, y1 = max(rows[0] - pad, 0), min(rows[1] + pad, pix.height)
    x0, x1 = max(cols[0] - pad, 0), min(cols[1] + pad, pix.width)
    if (x1 - x0) < 12 or (y1 - y0) < 12:
        return None
    img = Image.fromarray(a[y0:y1, x0:x1])
    return img.resize(((x1 - x0) * 3, (y1 - y0) * 3), Image.LANCZOS)


def sentence_openers(book_json) -> Counter:
    """Words that actually open sentences in this book."""
    cnt = Counter()
    for ch in book_json["chapters"]:
        for b in ch["blocks"]:
            for sent in re.split(r"(?<=[.!?…])\s+", b["text"]):
                m = re.match(r"[‘“\"']?([A-Za-zÀ-ÿ’']+)", sent.strip())
                if m:
                    cnt[m.group(1).lower()] += 1
    return cnt


def book_vocabulary(book_json) -> set:
    """Words used repeatedly in the book itself (names the dictionary lacks)."""
    cnt = Counter()
    for ch in book_json["chapters"]:
        for b in ch["blocks"]:
            for tok in WORD_RE.findall(b["text"]):
                if tok[:1].isupper():
                    cnt[tok.lower()] += 1
    return {t for t, c in cnt.items() if c >= 3}


def cap_rect(page):
    """Box where the drop cap sits: left of, and above, the first body line."""
    lines = []
    for b in page.get_text("dict")["blocks"]:
        if b["type"] != 0:
            continue
        for l in b["lines"]:
            t = "".join(s["text"] for s in l["spans"]).strip()
            if t and not DATELINE_RE.match(t) and l["bbox"][1] > 0.15 * page.rect.height:
                lines.append(l["bbox"])
    if not lines:
        return None
    lines.sort(key=lambda bb: bb[1])
    first = lines[0]
    left = min(bb[0] for bb in lines)
    top = first[1]
    return pymupdf.Rect(left - 6, top - 12, max(first[0] + 2, left + 30), top + 70)


def ocr(book) -> list[dict]:
    """Run OCR over every chapter whose first paragraph starts lowercase."""
    from .textutil import dictionary
    book_json = json.loads(book.book_json.read_text(encoding="utf-8"))
    vocab = set(dictionary()) | book_vocabulary(book_json)
    openers = sentence_openers(book_json)
    unresolved = [ch for ch in book_json["chapters"]
                  if (next((b for b in ch["blocks"] if b["type"] == "para"), {}) or {})
                  .get("text", " ")[0:1].islower()]
    if not unresolved:
        print(f"{book.slug}: no unresolved drop caps")
        book.dropcap_ocr.write_text("[]", encoding="utf-8")
        return []
    if not tesseract_available():
        print(f"{book.slug}: tesseract not found - skipping OCR of "
              f"{len(unresolved)} drop caps (fix by hand in overrides/dropcaps.json; "
              "see: bookpress healthcheck)")
        book.dropcap_ocr.write_text("[]", encoding="utf-8")
        return []

    doc = pymupdf.open(book.cfg["pdf"])
    results = []
    for ch in unresolved:
        first = next(b for b in ch["blocks"] if b["type"] == "para")
        page = doc[ch["start_page"]]
        rect = cap_rect(page)
        if rect is None:
            continue
        frag = (WORD_RE.findall(first["text"].split()[0]) or [""])[0]
        core = frag.strip("\u2019'.,;:!?")
        votes = []
        for dpi in (300, 450):
            img = crop_glyph(page, rect, dpi)
            if img is None:
                continue
            for psm in (10, 8, 13):
                got = ocr_glyph(img, psm)
                if len(got) == 1 and got.isalpha():
                    votes.append(got.upper())
        good = [c for c in dict.fromkeys(votes)
                if (c + core).lower() in vocab and openers.get((c + core).lower(), 0) >= 2]
        standalone = (core.lower() in vocab and len(core) > 1
                      and openers.get(core.lower(), 0) >= 2)
        unanimous = bool(votes) and len(set(votes)) == 1
        letter = good[0] if good else (votes[0] if (unanimous and standalone) else None)
        spacing = "glue"
        if letter and core.lower() in vocab and (letter + core).lower() not in vocab:
            spacing = "space"              # the cap is a word of its own ("A", "I")
        results.append({"page": ch["start_page"], "chapter": ch["number"], "frag": frag,
                        "votes": votes, "letter": letter, "spacing": spacing,
                        "word_ok": bool(good),
                        "dict_candidates": [c for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
                                            if (c + core).lower() in vocab],
                        "text": first["text"][:60]})
        print(f"p{ch['start_page']:>3} ch{str(ch['number']):>3} frag={frag:<10} "
              f"votes={votes} -> {letter}   {first['text'][:45]!r}")
    book.dropcap_ocr.write_text(json.dumps(results, indent=1), encoding="utf-8")
    ok = sum(1 for r in results if r["letter"])
    print(f"{book.slug}: {len(results)} unresolved caps, {ok} recovered by OCR")
    return results


def apply(book) -> list[dict]:
    """Apply manual overrides (first) and OCR votes to book.json."""
    book_json = json.loads(book.book_json.read_text(encoding="utf-8"))
    ocr_path = book.dropcap_ocr
    votes = {r["page"]: r for r in json.loads(ocr_path.read_text(encoding="utf-8"))} \
        if ocr_path.exists() else {}
    manual_path = book.override("dropcaps")
    manual = {m["page"]: m for m in json.loads(manual_path.read_text(encoding="utf-8"))} \
        if manual_path.exists() else {}
    log = []
    for ch in book_json["chapters"]:
        first = next((b for b in ch["blocks"] if b["type"] == "para"), None)
        if not first:
            continue
        page = ch["start_page"]
        before = first["text"]
        if page in manual:
            m = manual[page]
            if first["text"].startswith(m["old"]):
                first["text"] = m["new"] + first["text"][len(m["old"]):]
                log.append({"page": page, "chapter": ch["number"], "how": "manual",
                            "from": before[:60], "to": first["text"][:60]})
            else:
                print(f"!! override did not match p{page}: {before[:60]!r}")
        elif first["text"] and first["text"][0].islower():
            r = votes.get(page)
            if r and r["letter"]:
                letter = r["letter"]
                if r.get("spacing") == "space":
                    first["text"] = letter + " " + first["text"]
                elif first["text"][0] == letter.lower():
                    first["text"] = letter + first["text"][1:]
                else:
                    first["text"] = letter + first["text"]
                log.append({"page": page, "chapter": ch["number"],
                            "how": "ocr" if r["word_ok"] else "ocr-unvalidated",
                            "from": before[:60], "to": first["text"][:60]})
            else:
                log.append({"page": page, "chapter": ch["number"], "how": "UNRESOLVED",
                            "from": before[:60], "to": first["text"][:60]})
        first["dropcap"] = True
    book.book_json.write_text(json.dumps(book_json, indent=1), encoding="utf-8")
    book.dropcap_log.write_text(json.dumps(log, indent=1), encoding="utf-8")
    bad = [l for l in log if l["how"] == "UNRESOLVED"]
    print(f"{book.slug}: {len(log)} drop caps handled "
          f"({sum(1 for l in log if l['how'] == 'manual')} manual, "
          f"{sum(1 for l in log if l['how'].startswith('ocr'))} ocr, {len(bad)} unresolved)")
    for l in bad:
        print(f"   UNRESOLVED p{l['page']} {l['from']}")
    return log


def apply_block_repairs(book) -> list[dict]:
    """overrides/blocks.json - hand-written replacements for paragraphs the OCR
    mangled beyond rule-based repair:

      [{"page": 63, "merge_first": 4, "expect": "substring that must be present",
        "new": "the corrected paragraph"}]
    """
    path = book.override("blocks")
    if not path.exists():
        print(f"{book.slug}: no block repairs")
        return []
    book_json = json.loads(book.book_json.read_text(encoding="utf-8"))
    edits = json.loads(path.read_text(encoding="utf-8"))
    by_page = {ch["start_page"]: ch for ch in book_json["chapters"]}
    applied = []
    for e in edits:
        ch = by_page.get(e["page"])
        if not ch:
            print(f"!! no chapter starting on page {e['page']}")
            continue
        k = e.get("merge_first", 1)
        blocks = [b for b in ch["blocks"] if b["type"] == "para"][:k]
        merged = " ".join(b["text"] for b in blocks)
        if e.get("expect") and e["expect"] not in merged:
            print(f"!! p{e['page']} expect {e['expect']!r} not in {merged[:80]!r}")
            continue
        footnotes = [f for b in blocks for f in b.get("footnotes", [])]
        new = {"type": "para", "text": e["new"], "page": e["page"], "dropcap": True,
               "starts_page": True, "ends_page": False, "indented_start": False,
               "ends_full": False}
        if footnotes:
            new["footnotes"] = footnotes
        idx = next(i for i, b in enumerate(ch["blocks"]) if b is blocks[0])
        ch["blocks"] = ch["blocks"][:idx] + [new] + ch["blocks"][idx + len(blocks):]
        applied.append({"page": e["page"], "chapter": ch["number"],
                        "merged": merged[:80], "new": e["new"][:80]})
    book.book_json.write_text(json.dumps(book_json, indent=1), encoding="utf-8")
    (book.dir / "block-repairs.json").write_text(json.dumps(applied, indent=1),
                                                 encoding="utf-8")
    print(f"{book.slug}: {len(applied)}/{len(edits)} block repairs applied")
    return applied
