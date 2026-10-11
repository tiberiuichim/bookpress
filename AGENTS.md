# AGENTS.md — working on bookpress

**Read [README.md](README.md) first.** It explains the pipeline, the config file
and the review workflow. This file only adds how to work *with an agent*.

## What this project does

`bookpress` turns a scanned + OCR'd book PDF into a clean EPUB and a typeset
print PDF. It works in stages; each stage writes a JSON artifact under
`books/<slug>/` so you can inspect and re-run any step independently.

```
extract -> structure -> dropcaps -> fixes -> clean -> cover -> render
```

## Ground rules for an agent

1. **Never edit the input PDF** and never write into `inbox/`.
2. **Never hand-edit `book.json` / `book.clean.json`.** They are generated. All
   human corrections go into `books/<slug>/overrides/*.json` (see the table in
   README.md) and are re-applied on every run.
3. **Always review before declaring success.** After a run, read:
   - `books/<slug>/report.json` — chapters found, footnotes, pages dropped;
   - `books/<slug>/changes.txt` — every text change the cleaner made;
   - `bookpress status <slug>` — unresolved drop caps and missing stages.
   A conversion is not done until these look sane.
4. **Re-run, don't patch.** Fix an override file or a config regex, then re-run
   the affected stage (`bookpress clean <slug>`, `bookpress render <slug>`) or
   `bookpress all <slug>`. The pipeline is deterministic.
5. Keep `books/`, `inbox/` and `books.toml` out of git (already in `.gitignore`);
   commit code, docs and `examples/` only.

## Working loop for a new book

```bash
make health                    # first: what tools are actually usable here
make book PDF=inbox/scan.pdf SLUG=mybook TITLE="…" AUTHOR="…"
make review SLUG=mybook        # status + audit trail + unresolved words
```

The `Makefile` wraps the common sequences; `make help` lists them. Underneath it is
plain CLI usage:

```bash
bookpress healthcheck            # first: what tools are actually usable here
bookpress add --pdf inbox/scan.pdf --slug mybook --title "…" --author "…"
bookpress all mybook
bookpress status mybook          # chapters / footnotes / dropped pages / unresolved caps
bookpress review mybook          # audit of every change
bookpress words mybook           # words still outside the vocabulary
```

Typical fixes, in order of payoff:

| symptom | where to fix |
|---|---|
| chapter count wrong, headings missing | `heading` / `chapter` regex in `books.toml` |
| footnotes in the middle of the prose | `footnote_start` regexes; check `report.json` for the page |
| paragraphs chopped into single lines | the page is set in a narrower measure — use `overrides/blocks.json` |
| a chapter starts mid-word | `overrides/dropcaps.json` (`bookpress status` lists these) |
| names mangled ("Robcrt" for "Robert") | `overrides/words.json` |
| a paragraph is unsalvageable | `overrides/blocks.json` with `expect` as a safety substring |
| dialogue quotes wrong or unbalanced | `quote_substitute` / `open_quote_chars` in `books.toml` |

## Changing the code

* Stages are modules in `bookpress/`: `extract.py`, `structure.py`,
  `dropcaps.py`, `clean.py`, `render.py`, `cover.py`; the CLI is `bookpress/cli.py`.
* Shared helpers (bounded edit distance, system dictionary, word lists) live in
  `bookpress/textutil.py`; language data lives in `bookpress/data/*.txt`.
* Per-book settings come from `bookpress/config.py` (`Book.get(...)`); add new
  knobs to `DEFAULTS` there rather than hard-coding them in a stage.
* **Every text change made by `clean.py` must be logged** with
  `self.log(rule, page, chapter, before, after)` so it shows up in the audit.
* Rules must be conservative: prefer "do nothing" over a plausible-but-possibly
  wrong rewrite, and gate word changes on frequency, not just edit distance.

### Typst gotchas (the print PDF)

* In markup mode, function calls need `#`: `#block(...)`, `#pagebreak()`,
  and inside a content block `#footnote([...])`.
* `par(spacing: 0pt)` mis-lays-out consecutive paragraphs — keep spacing ≥ 0.4em.
* Render the drop cap inline (`par[#text(2.2em)[W]ho ...]`); a `grid`/float for
  the initial swallows the first-line indent of the *next* paragraph.
* `bookpress render` skips the PDF if the `typst` python bindings are missing —
  run it with the interpreter that has them (`.venv/bin/python -m bookpress.cli …`).

## Verifying your work

```bash
make check                      # lint + health + selftest (what CI runs)
python tests/selftest.py        # synthetic scan -> full pipeline -> assertions
```

It also runs `bookpress healthcheck` and asserts the required dependencies resolve,
so a broken environment fails the test rather than silently skipping stages.

Add an assertion there whenever you fix a rule, so the fix stays fixed. The
fixture lives in `tests/make_fixture.py`; it deliberately reproduces the nasty
parts of a scan (fragmented spans, image-only drop cap, paragraph split across a
page break, footnote band, page numbers, front matter).

## Things not to do

* Do not add network access, downloads or cloud OCR — everything runs locally.
* Do not add DRM removal or support for encrypted PDFs.
* Do not commit example books, scans or conversion output.
