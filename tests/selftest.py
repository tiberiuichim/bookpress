#!/usr/bin/env python3
"""End-to-end self test: build a synthetic scan, run every stage, check the result.

    python tests/selftest.py

No network, no real books, no external services.  pandoc / typst / tesseract are
optional: the stages that need them report that they skipped, and the test only
asserts on what is always produced (geometry, structure, cleaning, Typst source).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

from tests.make_fixture import build as build_fixture          # noqa: E402

FAILURES: list[str] = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        FAILURES.append(msg)


def run(argv, cwd):
    print(f"$ bookpress {' '.join(argv)}")
    r = subprocess.run([sys.executable, "-m", "bookpress.cli", *argv], cwd=cwd,
                       env={**os.environ, "PYTHONPATH": str(ROOT)})
    assert r.returncode == 0, f"bookpress {' '.join(argv)} exited {r.returncode}"


def health(root: Path) -> int:
    from bookpress import doctor
    code, rows = doctor.report(root, as_json=True)
    by_name = {r["name"]: r["status"] for r in rows}
    print("--- healthcheck ---")
    check(code == 0, "healthcheck exits clean")
    for name in ("python", "PyMuPDF", "pillow", "numpy"):
        check(by_name[name] == doctor.OK, f"required dependency present: {name}")
    check(by_name["fonts"] == doctor.OK, "print font resolvable by Typst")
    return 0


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="bookpress-selftest-"))
    pdf = build_fixture(tmp / "inbox" / "fixture.pdf")

    (tmp / "books.toml").write_text(
        f'[book.fixture]\npdf = "{pdf}"\ntitle = "A Book of No Import"\n'
        'author = "An Author"\nseries = "Small Series"\n', encoding="utf-8")

    run(["all", "fixture"], tmp)
    run(["list"], tmp)
    run(["status", "fixture"], tmp)
    run(["review", "fixture"], tmp)
    run(["words", "fixture", "--limit", "5"], tmp)

    work = tmp / "books" / "fixture"
    book = json.loads((work / "book.clean.json").read_text(encoding="utf-8"))
    report = json.loads((work / "report.json").read_text(encoding="utf-8"))

    print("\n--- structure ---")
    check(len(book["chapters"]) == 3, f"3 chapters recovered (got {len(book['chapters'])})")
    check(book["chapters"][0]["number"] == 1, "chapter numbering parsed from the heading")
    check(book["chapters"][2]["number"] is None, "unnumbered section (EPILOGUE) kept")
    datelines = [b for ch in book["chapters"] for b in ch["blocks"] if b["type"] == "dateline"]
    check(len(datelines) == 2, f"2 datelines recovered (got {len(datelines)})")

    print("--- paragraphs ---")
    paras = [b for ch in book["chapters"] for b in ch["blocks"] if b["type"] == "para"]
    check(all(len(p["text"]) > 120 for p in paras),
          "no paragraph left as a single line fragment")
    joined = " ".join(p["text"] for p in paras)
    check("ofThe" not in joined, "glued token split (ofThe -> of The)")
    check("committee was read aloud before the doors were locked" in joined,
          "paragraph merged across the page break")

    print("--- drop caps ---")
    first = next(b for b in book["chapters"][0]["blocks"] if b["type"] == "para")
    check(first["text"].split()[0][0].isupper(),
          f"lost initial recovered: {first['text'][:24]!r}")

    print("--- footnotes ---")
    fns = [f for ch in book["chapters"] for b in ch["blocks"] for f in b.get("footnotes", [])]
    check(len(fns) >= 1, f"footnote captured (got {len(fns)})")
    check(any("ledger" in f for f in fns), "footnote text intact")
    check(not any("footnote about the ledger" in p["text"] for p in paras),
          "footnote not left in the body")

    print("--- quotes ---")
    check(any("“You will not sign" in p["text"] or "‘You will not sign" in p["text"]
              for p in paras), "straight quotes made typographic")

    print("--- front matter ---")
    check(any("Copyright" in t for ch in book["front"] for t in
              [b["text"] for b in ch["blocks"]]), "copyright page kept")

    print("--- outputs ---")
    typ = work / "pdf" / "fixture.typ"
    check(typ.exists(), "Typst source written")
    if typ.exists():
        src = typ.read_text(encoding="utf-8")
        check("#footnote([" in src, "real footnotes in the Typst source")
        check("cap: [" in src, "drop cap in the Typst source")
        check("#chapterhead(" in src, "chapter heads in the Typst source")
    check((work / "epub" / "fixture.md").exists(), "EPUB markdown written")
    for name, needed in (("epub", "pandoc"), ("pdf", None)):
        produced = list((work / name).glob(f"*.{name}"))
        if produced:
            check(bool(produced), f"{name.upper()} built: {produced[0].name}")

    health(tmp)

    print("\n" + ("ALL CHECKS PASSED" if not FAILURES else f"{len(FAILURES)} FAILURES"))
    for f in FAILURES:
        print("  -", f)
    print(f"work area kept for inspection: {work}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main())
