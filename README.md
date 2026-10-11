# bookpress

Convert a **scanned + OCR'd book PDF** into

* a reflowable **EPUB** (with cover, TOC and real footnotes), and
* a **typeset print PDF** (page size of your choice, drop caps, justified
  indented prose, running footnotes, title/copyright/epigraph pages),

while keeping the structure of the original book: chapter opens, section
headings, datelines ("Place, 1 January 1900"), paragraphs, dialogue and
footnotes.

The starting point is a scan whose embedded OCR text layer is *readable but
structurally useless*. bookpress rebuilds the book from **geometry** — where
each glyph sits on the page — and then repairs the text with conservative,
fully logged rules.

---

## Install

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e .
```

`pip install -e .` also gives you the `bookpress` command. Check your machine with:

```bash
bookpress healthcheck
```

It lists the python libraries, external tools and fonts, and says what each missing
piece costs you (no pandoc means no EPUB; no tesseract means drop caps have to be
fixed by hand). It exits non-zero only when something *required* is missing. The font
row asks Typst itself whether a family resolves, so fonts bundled with Typst count.

Optional system tools:

| tool | needed for |
|---|---|
| `pandoc` | EPUB output |
| `tesseract` | re-OCR of damaged drop caps |
| `/usr/share/dict/{british-,american-}english` | spelling vocabulary for the repair rules |
| a serif font (Libertinus Serif, EB Garamond, …) | the print PDF |

## Quickstart

```bash
bookpress add --pdf inbox/mybook.pdf --slug mybook \
              --title "Title of the Book" --author "An Author" \
              --series "Optional Series" --page a5 --pt 11

bookpress all mybook          # extract -> structure -> dropcaps -> fixes -> clean -> cover -> render
bookpress status mybook       # what ran, what is still unresolved
bookpress review mybook       # every text change the cleaner made, grouped by rule
```

Output lands in `books/<slug>/`:

```
books/<slug>/
├── geometry.json          stage 1: per-page line/span geometry
├── book.json              stage 2-3: structured chapters/blocks
├── book.clean.json        stage 4: cleaned text (what gets rendered)
├── report.json            what was dropped / guessed, per page
├── changes.tsv/.txt       audit trail of every text change
├── dropcap-ocr.json, dropcap-log.json
├── overrides/             your hand-written repairs (see below)
├── epub/<Title>.epub, epub/<Title>.md, epub/cover.jpg
└── pdf/<Title>.pdf,  pdf/<slug>.typ
```

## How it works

| stage | what it does |
|---|---|
| `extract` | dumps every text span with its box, plus a baseline per line (an oversized initial letter otherwise wrecks reading order) |
| `structure` | splits front matter from body, finds chapter headings, datelines, page numbers and the footnote band, re-joins lines that the OCR split, assembles paragraphs from indents/gaps/right-margin reach, merges paragraphs split across pages, de-hyphenates, recovers lost drop caps |
| `dropcaps` | crops the drop-cap box from the page image, OCRs one glyph at two resolutions × three segmentation modes, accepts it only if the result is a word this book really uses to start a sentence |
| `fixes` | applies hand-written paragraph repairs from `overrides/blocks.json` |
| `clean` | punctuation, quote direction, missing quote pairs, footnote markers, glued tokens, word repair gated by frequency — every change logged |
| `cover` | cuts the front cover out of the scan for the EPUB |
| `render` | EPUB via pandoc, print PDF via Typst |

Run a single stage any time: `bookpress clean mybook`, `bookpress render mybook`, …
`--all` runs the stage for every book in `books.toml`.

## Tuning per book

`books.toml` holds one `[book.<slug>]` section per book; see
[`books.example.toml`](books.example.toml). Everything has a default:

```toml
[book.mybook]
pdf = "inbox/mybook.pdf"
title = "Title of the Book"
author = "An Author"
lang = "en"            # hyphenation + EPUB language
page = "a5"            # a4 | a5 | a6 | 5x8 | 6x9 | 5.5x8.5 | any Typst paper
pt = 11                # body size
font = "Libertinus Serif"
cover_page = 1

# regexes, if the defaults do not fit this book
heading = '^(CHAPTER|PART|BOOK)\s*[\d.IVX]*\s*$'
footnote_start = ['^[*†]\s*\S', '^[A-Za-z]{0,3}[.,]?\s?\d{2,4}\b']
quote_substitute = ""      # e.g. "5" if the scan renders ’ as 5
open_quote_chars = ["£"]   # chars the OCR misreads as an opening quote
```

Scans differ in *how* they break text, so several rules are auto-detected from
the book itself (a digit that keeps appearing where a closing quote belongs, the
book's own vocabulary, which words open its sentences). Override them when the
auto-detection guesses wrong.

## Reviewing, then repairing

Never trust a conversion blindly — read the audit first:

```bash
bookpress status mybook     # chapters found, footnotes, pages dropped, unresolved caps
bookpress review mybook     # every change, grouped: word / quote / footnote / marker
bookpress words mybook      # words still outside the dictionary + book vocabulary
```

Then fix what is wrong by adding files under `books/<slug>/overrides/` and
re-running (`bookpress fixes clean render` style, or just `bookpress all`):

| file | purpose |
|---|---|
| `overrides/words.json` | `[{"find": "survning", "replace": "surviving"}]` — literal replacements, applied before the automatic rules |
| `overrides/dropcaps.json` | `[{"page": 18, "old": "ho had", "new": "Who had"}]` — chapter initials the OCR could not recover |
| `overrides/blocks.json` | `[{"page": 63, "merge_first": 4, "expect": "substring", "new": "corrected paragraph"}]` — replace a whole paragraph (or merge several) when the scan mangled it |

Examples in [`examples/overrides/`](examples/overrides/). Overrides are applied
on every run, so a conversion is reproducible: the pipeline is deterministic and
safe to re-run after each edit.

## Self test

```bash
python tests/selftest.py
```

Builds a synthetic "scan" (fragmented spans, an image-only drop cap, a paragraph
split across a page break, a footnote band, page numbers, front matter), runs the
whole pipeline and asserts on the result. Useful as a regression test after you
change a rule.

## Known limits

* OCR letter-level errors that are themselves valid words are not fixed
  automatically — that is what `overrides/words.json` is for.
* Passages the original sets in a narrower measure (letters, poems, tables) come
  out as one short paragraph per line rather than a block quote.
* A page is treated as an illustration when its OCR is mostly non-words; if a
  real page is dropped, look for it in `report.json`.
* The print PDF assumes one column of prose, footnotes at the page bottom.
* Multi-language books need their own `data/*.txt` word lists (speech verbs,
  months, function words) for the dialogue/date rules to fire.

## Legal

Convert books you are allowed to convert. Nothing in here circumvents lending
locks or DRM: the input is a PDF you already have, and the output is yours.

MIT — see [LICENSE](LICENSE).
