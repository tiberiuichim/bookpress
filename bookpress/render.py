"""Stage 5 - render the cleaned book to an EPUB (pandoc) and a print PDF (Typst).

Both formats keep the structure recovered in stage 2: chapter opens with a drop
cap, italic datelines, indented justified paragraphs, real footnotes, plus the
title page / copyright page / epigraphs taken from the scan's front matter.

Needs the ``pandoc`` binary for the EPUB and the ``typst`` python bindings for
the PDF (``pip install typst``).
"""
from __future__ import annotations

import importlib.util
import json
import re
import subprocess
from pathlib import Path

JUNK_RE = re.compile(r"^[^A-Za-zÀ-ÿ]*$")
COPYRIGHT_RE = re.compile(r"\bCopyright\b|\bISBN\b|All rights reserved")
SERIES_PAGE_RE = re.compile(r"\bALSO BY\b|\bVolume \d|LARGE PRINT", re.I)

PAPER = {
    "a5": '"a5"', "a4": '"a4"', "a6": '"a6"', "a3": '"a3"', "letter": '"us-letter"',
    "5x8": "(12.7cm, 20.32cm)", "6x9": "(15.24cm, 21.59cm)",
    "5.5x8.5": "(13.97cm, 21.59cm)",
}

TEMPLATE = """
#set page(paper: __PAPER__,
         margin: (top: 2.3cm, bottom: 2.3cm, left: 2.2cm, right: 2.2cm),
         footer: context align(center, text(9.5pt, counter(page).display())))
#set text(font: "__FONT__", size: __PT__, lang: "__LANG__", hyphenate: true)
#set par(justify: true, leading: 0.62em, first-line-indent: 1.3em, spacing: 0.4em)

#let chapterhead(num, label) = {
  pagebreak(weak: true)
  block(breakable: false, {
    set par(first-line-indent: 0pt, justify: false)
    align(center, {
      if num != none {
        set text(tracking: 0.08em, size: 1.05em)
        [#num]
        v(0.9em)
      }
      set text(size: 1.5em, tracking: 0.03em)
      [#label]
    })
  })
  v(2.4em)
}

#let dateline(t) = {
  block(breakable: false, above: 1.1em, below: 1.1em, {
    set par(first-line-indent: 0pt, justify: false)
    align(center, emph(text(1.02em)[#t]))
  })
}

// An inline oversized initial: a grid/float here would swallow the first-line
// indent of the paragraph that follows it.
#let para(body, cap: none) = {
  if cap != none {
    par[#text(2.2em)[#cap]#body]
  } else {
    par[#body]
  }
}
"""


def tesc(s: str) -> str:
    """Escape Typst markup."""
    for a, b in (("\\", "\\\\"), ("#", r"\#"), ("$", r"\$"), ("@", r"\@"),
                 ("[", r"\["), ("]", r"\]"), ("*", r"\*"), ("_", r"\_"),
                 ("~", r"\~"), ("=", r"\="), ("<", r"\<"), (">", r"\>")):
        s = s.replace(a, b)
    return s


def printable_line(t: str) -> bool:
    """Keep readable front-matter lines, drop OCR noise."""
    if "\\" in t or "|" in t:
        return False
    return len(t) >= 4 and not JUNK_RE.match(t) and bool(re.search(r"[A-Za-zÀ-ÿ]{4,}", t))


def front_pages(book_json, attribution_re: str):
    """Split the scan's front matter into epigraphs and copyright lines."""
    act_re = re.compile(attribution_re)
    epigraphs, copyright_lines = [], []
    for page in book_json.get("front", []):
        texts = [b["text"].strip() for b in page["blocks"] if b["text"].strip()]
        if not texts:
            continue
        joined = " ".join(texts)
        if any(act_re.search(t) for t in texts):
            cur = []
            for t in texts:
                cur.append(t)
                if act_re.search(t):
                    epi = " ".join(cur).strip()
                    parts = re.split(r"(?<=[.!?…])\s+", epi)
                    if len(parts) > 1 and act_re.search(parts[-1]):
                        epigraphs.append((" ".join(parts[:-1]).strip(),
                                          parts[-1].strip().rstrip(".")))
                    else:
                        epigraphs.append((epi, ""))
                    cur = []
            if cur:
                epigraphs.append((" ".join(cur).strip(), ""))
        elif COPYRIGHT_RE.search(joined):
            copyright_lines.extend(t for t in texts if printable_line(t))
        elif SERIES_PAGE_RE.search(joined):
            continue                        # publisher's list of other titles
    return epigraphs, copyright_lines


def footnotes_of(block):
    return [f.strip() for f in block.get("footnotes", [])]


def chapter_label(ch) -> str:
    return f"Chapter {ch['number']}" if ch.get("number") else ch["raw_heading"].title()


# ---------------------------------------------------------------- EPUB
def to_markdown(book, book_json) -> int:
    title, author = book.title, book.cfg.get("author", "")
    series = book.cfg.get("series", "")
    L = [f'---\ntitle: "{title}"\nauthor: "{author}"\n']
    if series:
        L.append(f'subtitle: "{series}"')
    L.append(f'lang: {book.get("lang")}\n---\n')

    epigraphs, copyright = front_pages(book_json, book.get("epigraph_attribution"))
    L.append("# " + title + " {# .unnumbered}" + "\n\n" + author + "\n")
    if series:
        L.append("*" + series + "*\n")
    if copyright:
        L.append("## Copyright {# .unnumbered}\n")
        for line in copyright:
            L.append(line + "  \n")
    if epigraphs:
        L.append("## Epigraphs {# .unnumbered}\n")
        for text, attr in epigraphs:
            L.append(f"> {text}\n>\n> — {attr}\n" if attr else f"> {text}\n")

    n = 0
    for ch in book_json["chapters"]:
        L.append(f"\n# {chapter_label(ch)} {{unnumbered}}\n")
        for b in ch["blocks"]:
            if b["type"] == "dateline":
                L.append(f"*{b['text']}*\n")
                continue
            fns = footnotes_of(b)
            marks = []
            for _ in fns:
                n += 1
                marks.append(f"[^{n}]")
            L.append(b["text"] + "".join(marks) + "\n")
            for i, fn in enumerate(fns):     # pandoc needs the definition after
                L.append(f"[^{n - len(fns) + i + 1}]: {fn}\n")
    book.epub_dir.mkdir(parents=True, exist_ok=True)
    (book.epub_dir / f"{book.slug}.md").write_text("\n".join(L), encoding="utf-8")
    return n


def build_epub(book) -> Path | None:
    if subprocess.run(["which", "pandoc"], capture_output=True).returncode:
        print(f"{book.slug}: pandoc not found - skipping EPUB "
              "(see: bookpress healthcheck)")
        return None
    out = book.epub_dir / f"{book.title}.epub"
    cover = book.epub_dir / "cover.jpg"
    cmd = ["pandoc", str(book.epub_dir / f"{book.slug}.md"), "-o", str(out), "--toc",
           "--metadata", f"title={book.title}"]
    if cover.exists():
        cmd += ["--epub-cover-image", str(cover)]
    subprocess.run(cmd, check=True)
    print("wrote", out)
    return out


# ----------------------------------------------------------------- PDF
def to_typst(book, book_json) -> str:
    paper = PAPER.get(book.get("page"), book.get("page"))
    out = [TEMPLATE.replace("__PAPER__", paper)
                 .replace("__PT__", f"{book.get('pt')}pt")
                 .replace("__FONT__", book.get("font"))
                 .replace("__LANG__", book.get("lang"))]
    series = book.cfg.get("series", "")
    series_line = f"text(0.95em, tracking: 0.22em)[{tesc(series)}]" if series else ""
    out.append(f"""
// ---- title page ----
#block(above: 6em, {{
  set par(first-line-indent: 0pt, justify: false)
  align(center, {{
    {series_line}
    v(2.4em)
    text(2.5em)[{tesc(book.title)}]
    v(1.5em)
    text(1.15em, tracking: 0.1em)[{tesc(book.cfg.get('author', ''))}]
  }})
}})
#pagebreak()
""")
    epigraphs, copyright = front_pages(book_json, book.get("epigraph_attribution"))
    if copyright:
        out.append("#block(above: 1.5em, {\n  set text(size: 0.82em)\n"
                   "  set par(first-line-indent: 0pt, justify: false, spacing: 0.3em)\n")
        for line in copyright:
            out.append(f"  par[{tesc(line)}]\n")
        out.append("})\n#pagebreak()\n")
    if epigraphs:
        out.append("#block(breakable: false, above: 5em, {\n"
                   "  set par(first-line-indent: 0pt, justify: false)\n")
        for text, attr in epigraphs:
            out.append(f"  align(center, emph[{tesc(text)}])\n")
            if attr:
                out.append(f"  align(right, text(0.92em)[— {tesc(attr)}])\n")
            out.append("  v(2.4em)\n")
        out.append("})\n#pagebreak()\n")

    for ch in book_json["chapters"]:
        num = "none" if not ch.get("number") else str(ch["number"])
        out.append(f"#chapterhead({num}, [{tesc(chapter_label(ch))}])\n")
        first_para = True
        for b in ch["blocks"]:
            if b["type"] == "dateline":
                out.append(f"#dateline([{tesc(b['text'])}])\n")
                continue
            body = tesc(b["text"])
            for fn in footnotes_of(b):
                body += f" #footnote([{tesc(fn)}])"
            cap = ""
            if first_para and len(b["text"]) > 1:
                cap = f"cap: [{tesc(b['text'][0])}]"
                body = tesc(b["text"][1:])
            out.append(f"#para({cap})[{body}]\n")
            first_para = False
        out.append("\n")
    src = "".join(out)
    book.pdf_dir.mkdir(parents=True, exist_ok=True)
    (book.pdf_dir / f"{book.slug}.typ").write_text(src, encoding="utf-8")
    return src


def build_pdf(book) -> Path | None:
    if not importlib.util.find_spec("typst"):
        print(f"{book.slug}: the typst python bindings are missing in this "
              "interpreter - use the venv python, or see bookpress healthcheck")
        return None
    import typst
    out = book.pdf_dir / f"{book.title}.pdf"
    typst.compile(str((book.pdf_dir / f"{book.slug}.typ").resolve()), output=str(out))
    print("wrote", out)
    return out


def render(book) -> None:
    book_json = json.loads(book.clean_json.read_text(encoding="utf-8"))
    n = to_markdown(book, book_json)
    print(f"{book.slug}: {n} footnotes rendered")
    build_epub(book)
    to_typst(book, book_json)
    build_pdf(book)
