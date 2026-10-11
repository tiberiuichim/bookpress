"""Stage 2 - turn page geometry into a structured book (chapters / blocks).

Everything here is decided from geometry + pattern matching, because the OCR
layer's reading order cannot be trusted:

  * page numbers and footnote blocks are recognised in the bottom band,
  * a chapter heading starts a new chapter,
  * an italic "place, date" line becomes a dateline,
  * paragraph breaks come from first-line indents, vertical gaps and whether a
    line ran to the right margin (skew corrupts indents, margins are reliable),
  * the oversized chapter initial is recovered when the OCR dropped it.

Writes <slug>/book.json plus <slug>/report.json describing every guess.
"""
from __future__ import annotations

import json
import re
import statistics
from pathlib import Path

from .textutil import (DICT_PATHS, WORD_RE, clean_ws, dictionary, edit_distance,  # noqa: F401
                       read_words)

MONTHS = tuple(m.lower() for m in read_words("months.txt"))
PAGENUM_RE = re.compile(r"^[IVXLCM\s.\-]*\d{1,4}[\s.\-]*$")
MARKER_RE = re.compile(r"[*†]|(?<=[.,;:!?…])\d{1,3}(?=\s|$)")
DATELINE_RE = re.compile(
    r"^[A-ZÀ-Þ][\w'’.\- ]{1,28}?,?\s+(\d{1,2}\s+)?([A-Za-zà-ÿ]{3,12})\.?\s+\d{3,4}$")
TERMINAL = (".", "!", "?", "…", "”", "’", '"', ")")
DEHYPHEN_RE = re.compile(r"\b([A-Za-zÀ-ÿ'’]+)-\s+([a-zà-ÿ'’]+)\b")


def is_month(w: str) -> bool:
    """Fuzzy month match: scans mangle dates ('Fanuary', 'Septernber')."""
    w = w.lower()
    return any(edit_distance(w, m, 2) <= 2 and min(len(w), len(m)) >= 3 for m in MONTHS)


def is_garbage_page(page) -> bool:
    """Illustration / blank / stamp pages whose OCR is noise.

    Short pages are not automatically junk (epigraph pages are short), so a
    short page is only dropped when most of its words are not real words."""
    txt = "".join(l["text"] for l in page["lines"]).strip()
    if len(txt) < 40:
        return True
    sizes = [l["size"] for l in page["lines"]]
    tiny = sum(1 for s in sizes if s < 9)
    if sizes and tiny / len(sizes) > 0.6:
        return True
    if len(txt) < 140:
        words = re.findall(r"[a-zà-ÿ'’]{3,}", txt.lower())
        if not words:
            return True
        D = dictionary()
        return sum(1 for w in words if w in D) / len(words) < 0.6
    return False


def page_margin(lines) -> float:
    """Left margin = low percentile of line starts.  A percentile rather than the
    minimum, because dialogue-heavy pages are mostly indented lines."""
    xs = sorted(l["x0"] for l in lines)
    return xs[max(0, int(0.10 * (len(xs) - 1)))]


def Y0(l):
    return l.get("by0", l["y0"])


def Y1(l):
    return l.get("by1", l["y1"])


def coalesce_lines(lines, leading):
    """The OCR layer often splits one visual line into several 'lines'.  Merge
    fragments that share a baseline and abut horizontally."""
    out = []
    for l in lines:
        prev = out[-1] if out else None
        if prev and abs(Y0(l) - Y0(prev)) < 0.45 * leading:
            gap = l["x0"] - prev["x1"]
            sep = " " if gap > 3 else ""
            prev["text"] = (prev["text"].rstrip() + sep + l["text"].strip()).strip()
            prev["x1"] = max(prev["x1"], l["x1"])
            prev["x0"] = min(prev["x0"], l["x0"])
            prev["by1"] = max(prev["by1"], Y1(l))
        else:
            out.append(dict(l))
    return out


def join_lines(lines):
    return " ".join(clean_ws(l) for l in lines)


def join_hyphen_part(a, b, w1, w2):
    DICT = dictionary()
    if (w1 + w2).lower() in DICT:
        return a[:-1] + " " + b
    if w1.lower() in DICT and w2.lower() in DICT:
        return a + " " + b
    return a[:-1] + " " + b


def merge_hyphen(a, b):
    a, b = a.rstrip(), b.lstrip()
    if a.endswith("-") and a.split() and b.split():
        tail = WORD_RE.findall(a.split()[-1][:-1])
        head = WORD_RE.findall(b.split()[0])
        if tail and head and b.split()[0][0].islower():
            return join_hyphen_part(a, b, tail[0], head[0])
    return a + " " + b


def dehyphenate(text, report, chnum):
    DICT = dictionary()

    def repl(m):
        w1, w2 = m.group(1), m.group(2)
        if (w1 + w2).lower() in DICT:
            return w1 + w2
        if w1.lower() in DICT and w2.lower() in DICT:
            return m.group(0)
        if w1.lower() in DICT and len(w2) <= 3:
            return m.group(0)
        report["dehyphenated"].append({"chapter": chnum, "from": m.group(0), "to": w1 + w2})
        return w1 + w2

    return DEHYPHEN_RE.sub(repl, text)


def recover_dropcap(text, page, report):
    """The scan often loses the oversized initial, leaving 'ho had ...'.  If
    exactly one capital makes the fragment a dictionary word, that is the letter."""
    m = re.match(r"^([a-z’']+)(\s+)(.*)$", text, re.S)
    if not m:
        report["dropcaps_unresolved"].append({"page": page, "text": text[:50]})
        return text
    frag, _, rest = m.groups()
    DICT = dictionary()
    cands = [letter + frag for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
             if (letter + frag).lower() in DICT]
    if len(cands) == 1:
        fixed = cands[0] + " " + rest
        report["dropcaps_fixed"].append({"page": page, "from": text[:40], "to": fixed[:40]})
        return fixed
    report["dropcaps_unresolved"].append({"page": page, "text": text[:50],
                                          "candidates": cands})
    return text


class Structurer:
    def __init__(self, book):
        self.book = book
        self.heading_re = re.compile(book.get("heading"), re.I)
        self.chapter_re = re.compile(book.get("chapter"), re.I)
        self.footnote_res = [re.compile(p) for p in book.get("footnote_start")]
        self.report = {"dropped_pages": [], "headings": [], "datelines": [], "footnotes": [],
                       "dropcaps_fixed": [], "dropcaps_unresolved": [], "dehyphenated": [],
                       "page_numbers_dropped": 0}

    # ---- front / body split ------------------------------------------------
    def split_body(self, pages):
        first_body = None
        for p in pages:
            for l in p["lines"]:
                if self.chapter_re.match(clean_ws(l["text"])) and Y0(l) < 0.3 * p["h"]:
                    first_body = p["n"]
                    break
            if first_body is not None:
                break
        front = [p for p in pages if first_body is not None and p["n"] < first_body]
        body = [p for p in pages if first_body is None or p["n"] >= first_body]
        return first_body, front, body

    def front_matter(self, pages):
        out = []
        for p in pages:
            if is_garbage_page(p):
                self.report["dropped_pages"].append({"n": p["n"], "why": "garbage/illustration"})
                continue
            blocks = [{"text": clean_ws(l["text"]), "size": l["size"], "x0": l["x0"],
                       "y0": l["y0"]} for l in p["lines"] if l["text"].strip()]
            out.append({"page": p["n"], "blocks": blocks})
        return out

    def median_leading(self, pages):
        """Line spacing is stable across a scan; the reported font size is not."""
        samples = []
        for p in pages:
            bl = [l for l in p["lines"] if Y0(l) < 0.86 * p["h"]]
            for a, b in zip(bl, bl[1:]):
                dy = Y0(b) - Y0(a)
                if 10 < dy < 40:
                    samples.append(dy)
        return statistics.median(samples) if samples else 21.0

    # ---- per page ----------------------------------------------------------
    def classify(self, p):
        body, footnotes, pagenums = [], [], []
        in_fn = False
        h = p["h"]
        for l in p["lines"]:
            t = clean_ws(l["text"])
            if not t:
                continue
            low = Y0(l) > 0.80 * h
            if low and PAGENUM_RE.match(t) and len(t) <= 8:
                pagenums.append(l)
                continue
            if low and any(r.match(t) for r in self.footnote_res):
                footnotes.append(t)
                in_fn = True
                continue
            if low and in_fn:
                # once the footnote band starts it runs to the bottom of the page;
                # a line matching a footnote-start pattern opens a new footnote
                if footnotes and any(r.match(t) for r in self.footnote_res):
                    footnotes.append(t)
                elif footnotes:
                    footnotes[-1] = footnotes[-1].rstrip() + " " + t
                continue
            if len(t) <= 2 and l["size"] < 12:
                continue                      # stray specks OCR'd as characters
            body.append(l)
        self.report["page_numbers_dropped"] += len(pagenums)
        return body, footnotes

    def dateline_flags(self, body, leading, page_n):
        flags = []
        for i, l in enumerate(body):
            t = clean_ws(l["text"])
            gap_before = (Y0(l) - Y1(body[i - 1])) if i else 99
            m = DATELINE_RE.match(t)
            is_dl = bool(m) and len(t) < 45 and is_month(m.group(2)) and (
                i == 0 or gap_before > 1.4 * leading)
            if is_dl:
                self.report["datelines"].append({"page": page_n, "text": t})
            flags.append(is_dl)
        return flags

    def paragraphs(self, body, flags, page_n, chapter_start, leading):
        margin = page_margin(body)
        right_edge = max(l["x1"] for l in body)
        indents = [l["x0"] - margin for l in body if l["x0"] - margin >= 8]
        typ_indent = statistics.median(indents) if indents else 14.0
        dropcap_x = margin + max(typ_indent * 2.2, typ_indent + 22)

        paras, cur, prev = [], None, None
        in_col = False
        for l, is_dl in zip(body, flags):
            t = clean_ws(l["text"])
            if is_dl:
                if cur:
                    paras.append(cur)
                    cur = None
                paras.append({"type": "dateline", "lines": [t], "page": page_n})
                prev, cur = l, None
                continue
            dx = l["x0"] - margin
            indented = dx >= 8.0
            col = chapter_start and l["x0"] > dropcap_x
            if cur is None:
                in_col = col               # column mode only if the paragraph opens in it
            elif not in_col:
                col = False
            else:
                in_col = col
            gap = (Y0(l) - Y1(prev)) if prev else 99
            big_gap = gap > 1.45 * leading
            new = True if cur is None else (False if in_col else (indented or big_gap))
            if new:
                if cur:
                    paras.append(cur)
                cur = {"type": "para", "lines": [t], "page": page_n, "indented": indented,
                       "ends_full": l["x1"] > right_edge - 18}
            else:
                cur["lines"].append(t)
                cur["ends_full"] = l["x1"] > right_edge - 18
            prev = l
        if cur:
            paras.append(cur)
        return paras

    def run(self, geometry_path: Path):
        data = json.loads(Path(geometry_path).read_text(encoding="utf-8"))
        pages = data["pages"]
        first_body, front_pages, body_pages = self.split_body(pages)
        front = self.front_matter(front_pages)
        leading = self.median_leading(body_pages)

        chapters, cur, pending_footnote = [], None, None
        for p in body_pages:
            h = p["h"]
            if is_garbage_page(p):
                self.report["dropped_pages"].append({"n": p["n"], "why": "garbage/illustration"})
                continue

            body, footnotes = self.classify(p)

            # a footnote can wrap onto the next page
            if footnotes and pending_footnote and not pending_footnote["text"].endswith(TERMINAL):
                pending_footnote["text"] = pending_footnote["text"].rstrip() + " " + footnotes.pop(0)
            for fn in footnotes:
                pending_footnote = {"page": p["n"], "text": fn}
                self.report["footnotes"].append(pending_footnote)

            heading, heading_line = None, None
            for l in body:
                t = clean_ws(l["text"])
                if self.heading_re.match(t) and Y0(l) < 0.30 * h:
                    heading, heading_line = t, l
                    break
            if heading:
                body = [l for l in body if l is not heading_line]
                m = self.chapter_re.match(heading)
                cur = {"kind": "chapter" if m else "section",
                       "number": int(m.group(1)) if m else None,
                       "raw_heading": heading, "start_page": p["n"], "blocks": []}
                chapters.append(cur)
                self.report["headings"].append({"page": p["n"], "text": heading})
            if cur is None or not body:
                continue

            body = coalesce_lines(body, leading)
            flags = self.dateline_flags(body, leading, p["n"])
            paras = self.paragraphs(body, flags, p["n"], p["n"] == cur["start_page"],
                                    leading)

            for idx, para in enumerate(paras):
                if para["type"] == "dateline":
                    cur["blocks"].append({"type": "dateline", "text": para["lines"][0],
                                          "page": p["n"]})
                    continue
                cur["blocks"].append({
                    "type": "para", "text": join_lines(para["lines"]), "page": p["n"],
                    "indented_start": para["indented"], "ends_full": para["ends_full"],
                    "starts_page": idx == 0 or paras[idx - 1]["type"] == "dateline",
                    "ends_page": idx == len(paras) - 1})

            # a line that ran to the right margin continues, whatever the
            # (skew-corrupted) indent says
            fixed = []
            for b in cur["blocks"]:
                prev = fixed[-1] if fixed else None
                if (b["type"] == "para" and prev and prev["type"] == "para"
                        and prev.get("page") == b.get("page") and prev.get("ends_full")
                        and not prev["text"].rstrip().endswith(TERMINAL)):
                    prev["text"] = merge_hyphen(prev["text"], b["text"])
                    prev["ends_full"] = b.get("ends_full")
                    prev["footnotes"] = prev.get("footnotes", []) + b.get("footnotes", [])
                    continue
                fixed.append(b)
            cur["blocks"] = fixed

            if footnotes:
                page_paras = [b for b in cur["blocks"]
                              if b["type"] == "para" and b.get("page") == p["n"]]
                # anchor on the paragraph that still shows a footnote marker
                anchor = next((b for b in page_paras if MARKER_RE.search(b["text"])), None)
                anchor = anchor or (page_paras[-1] if page_paras else None)
                if anchor:
                    anchor.setdefault("footnotes", []).extend(footnotes)

        self.merge_across_pages(chapters)
        for ch in chapters:
            for b in ch["blocks"]:
                if b["type"] == "para":
                    b["text"] = dehyphenate(b["text"], self.report, ch["number"])
        for ch in chapters:
            first = next((b for b in ch["blocks"] if b["type"] == "para"), None)
            if not first:
                continue
            first["dropcap"] = True
            if first["text"] and first["text"][0].islower():
                first["text"] = recover_dropcap(first["text"], ch["start_page"], self.report)

        nchars = sum(len(b["text"]) for ch in chapters for b in ch["blocks"])
        out = {"meta": {"name": self.book.slug, "source": data["source"], "leading": leading,
                        "first_body_page": first_body, "n_chapters": len(chapters)},
               "front": front, "chapters": chapters}
        return out

    def merge_across_pages(self, chapters):
        for ch in chapters:
            merged = []
            for b in ch["blocks"]:
                prev = merged[-1] if merged else None
                if (b["type"] == "para" and prev and prev["type"] == "para"
                        and prev.get("ends_page") and b.get("starts_page")
                        and not b.get("indented_start")
                        and not prev["text"].rstrip().endswith(TERMINAL)):
                    prev["text"] = merge_hyphen(prev["text"], b["text"])
                    prev["footnotes"] = prev.get("footnotes", []) + b.get("footnotes", [])
                    prev["ends_page"] = b.get("ends_page")
                    continue
                merged.append(b)
            ch["blocks"] = merged


def structure(book) -> dict:
    st = Structurer(book)
    data = st.run(book.geometry)
    book.ensure_dirs()
    book.book_json.write_text(json.dumps(data, indent=1), encoding="utf-8")
    book.report.write_text(json.dumps(st.report, indent=1), encoding="utf-8")
    nchars = sum(len(b["text"]) for ch in data["chapters"] for b in ch["blocks"])
    print(f"{book.slug}: {len(data['chapters'])} chapters, {nchars} chars body, "
          f"{len(st.report['dropped_pages'])} pages dropped, "
          f"{len(st.report['footnotes'])} footnotes, "
          f"{len(st.report['dropcaps_fixed'])} drop caps fixed, "
          f"{len(st.report['dropcaps_unresolved'])} unresolved")
    return data
