"""Build a synthetic 'scanned' book PDF to exercise the pipeline.

Everything the real scans throw at us is reproduced on purpose:

  * one visual line split across several text spans (OCR fragmentation),
  * an oversized chapter initial that exists only as an image, so the text
    layer starts mid-word,
  * first-line indents, and a paragraph deliberately split across a page break,
  * a footnote band at the bottom of a page, plus running page numbers,
  * front matter: title page, copyright page, epigraph page,
  * glued tokens ("ofThe") and straight quotes.

The wording is clean English, so the self-test asserts on *structure*, not on
spelling repair.
"""
from __future__ import annotations

import io
from pathlib import Path

import pymupdf
from PIL import Image, ImageDraw

W, H = 420.0, 595.0
LEFT, RIGHT = 60.0, 360.0
INDENT = 14.0
LEAD = 16.0
BODY = 11.0
FOOTNOTE_TOP = 500.0          # below this y, a line belongs to the footnote band

PARA = [
    "Beginning at nine o'clock, the committee met in the small room at the end "
    "of the corridor, and the matter of the ledger was discussed at length by "
    "every person present in the room that evening, until the lamp began to smoke.",
    "Nobody wished to open the second envelope, because the letter inside it "
    "was known to be unpleasant, and the secretary kept it closed until the "
    "end of the meeting came near at hand for everyone.",
    "Certainly the silence that followed was the longest anyone in the house "
    "could remember, since the war had ended in the spring of the previous "
    "year, and nobody spoke of it again before the morning.",
]

DIALOG = [
    "\"You will not sign that paper,\" said the old man, quietly. \"I would "
    "rather lose the house than sign it, and I have told the clerk so twice "
    "already, in this very room, before the witnesses.\"",
    "\"Then we are agreed,\" the clerk answered, and he closed the folder and "
    "put it back into the drawer by the window, where the lamp threw no light "
    "at all on the papers that were stacked there.\"",
]

CHAPTERS = [
    ("CHAPTER 1", "Nowhere, 5 January 1900"),
    ("CHAPTER 2", "Elsewhere, 12 February 1901"),
    ("EPILOGUE", None),
]


class Sheet:
    """Draws a book page the way a scanner sees it."""

    def __init__(self, doc):
        self.doc = doc
        self.page = None
        self.y = 0.0
        self.pageno = 1

    def new_page(self):
        self.page = self.doc.new_page(width=W, height=H)
        self.y = 90.0
        self.page.insert_text((W / 2 - 6, 560), str(self.pageno),
                              fontsize=9.5, fontname="helv")
        self.pageno += 1
        return self.page

    def _span(self, x, y, text, size):
        self.page.insert_text((x, y), text, fontsize=size, fontname="helv")
        return x + pymupdf.get_text_length(text, fontname="helv", fontsize=size)

    def paragraph(self, text, first_x=LEFT, size=BODY, cap: str | None = None):
        """Word-wrap a paragraph; the first line is indented (or starts after a
        drop cap), continuation lines start at the left margin."""
        words = text.split()
        x = first_x
        if cap:
            # the initial exists only as an image: the text layer starts mid-word
            first = words.pop(0)
            _cap_image(self.page, first[0], LEFT, self.y - 26)
            words.insert(0, first[1:])
            x = LEFT + 46
        line, line_x = [], x
        while words:
            w = words.pop(0)
            trial = (line + [w])
            width = sum(pymupdf.get_text_length(t + " ", fontname="helv", fontsize=size)
                        for t in trial)
            if x + width > RIGHT and line:
                self._emit_line(line, x, size)
                self.y += LEAD
                if self.y > FOOTNOTE_TOP - LEAD:
                    self.new_page()
                x = LEFT
                line = [w]
            else:
                line = trial
        if line:
            self._emit_line(line, x, size)
            self.y += LEAD

    def _emit_line(self, words, x, size):
        fragments = 3 if len(words) > 6 else 1
        per = max(1, -(-len(words) // fragments))
        for i in range(0, len(words), per):
            part = " ".join(words[i:i + per])
            trail = " " if i + per < len(words) else ""
            self.page.insert_text((x, self.y), part + trail, fontsize=size, fontname="helv")
            x += pymupdf.get_text_length(part + trail, fontname="helv", fontsize=size)

    def heading(self, text):
        self.page.insert_text((W / 2 - 30, self.y), text, fontsize=13, fontname="helv")
        self.y += 3 * LEAD

    def dateline(self, text):
        self.page.insert_text((LEFT + 40, self.y), text, fontsize=BODY, fontname="helv")
        self.y += 2 * LEAD

    def footnote(self, lines):
        for i, t in enumerate(lines):
            self.page.insert_text((LEFT, FOOTNOTE_TOP + i * LEAD), t, fontsize=9.5,
                                  fontname="helv")


def _cap_image(page, letter, x, y, size=34):
    im = Image.new("L", (size + 10, size + 10), 255)
    ImageDraw.Draw(im).text((4, 2), letter, fill=0)
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    page.insert_image(pymupdf.Rect(x, y, x + size + 10, y + size + 10),
                      stream=buf.getvalue())


def build(out_path: Path) -> Path:
    doc = pymupdf.open()

    p = doc.new_page(width=W, height=H)
    p.insert_text((140, 200), "A BOOK OF NO IMPORT", fontsize=20, fontname="helv")
    p.insert_text((170, 232), "An Author", fontsize=13, fontname="helv")

    p = doc.new_page(width=W, height=H)
    for i, line in enumerate(("This edition published in 1912 by Some Publisher Ltd",
                              "Copyright 1912 by the Author of the Book",
                              "ISBN 978 0 00000 000 0",
                              "All rights reserved")):
        p.insert_text((LEFT, 140 + i * LEAD), line, fontsize=9.5, fontname="helv")

    p = doc.new_page(width=W, height=H)
    p.insert_text((LEFT, 180), "We laboured in the vineyard all the summer long.",
                  fontsize=BODY, fontname="helv")
    p.insert_text((LEFT, 196), "The Long Work, Act II, Scene iii", fontsize=BODY,
                  fontname="helv")

    sheet = Sheet(doc)
    for ci, (heading, dateline) in enumerate(CHAPTERS):
        sheet.new_page()
        sheet.heading(heading)
        if dateline:
            sheet.dateline(dateline)
        paras = [PARA[ci % len(PARA)], DIALOG[ci % len(DIALOG)],
                 PARA[(ci + 1) % len(PARA)]]
        for pi, text in enumerate(paras):
            if pi == 0:
                sheet.paragraph(text, cap="B")      # initial only as an image
            else:
                sheet.paragraph(text, first_x=LEFT + INDENT)
            if ci == 0 and pi == 0:
                sheet.footnote(["* A footnote about the ledger, set low on the",
                                "page and continuing onto the next line."])
            if ci == 1 and pi == 1:
                # glued token + a paragraph that runs over the page break
                sheet.paragraph("The rest of the report ofThe committee was read "
                                "aloud before the doors were locked, and the keys "
                                "were given to the porter for the night, who kept "
                                "them until the morning bell was rung in the yard.")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(out_path))
    doc.close()
    return out_path


if __name__ == "__main__":
    import sys
    print(build(Path(sys.argv[1] if len(sys.argv) > 1 else "fixture.pdf")))
