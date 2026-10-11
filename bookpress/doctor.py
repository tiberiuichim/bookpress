"""Environment check: what is installed, what is missing, and what each gap costs.

    bookpress healthcheck [--json]
"""
from __future__ import annotations

import importlib
import importlib.metadata as md
import json
import platform
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from .config import ConfigError, load
from .textutil import DICT_PATHS, data_file

OK, WARN, MISS = "ok", "warn", "missing"
MARK = {OK: "✓", WARN: "!", MISS: "✗"}


def _module(module: str, dist: str | None = None):
    """(status, version) for an importable python dependency."""
    try:
        mod = importlib.import_module(module)
    except ImportError:
        return None, "not installed"
    ver = getattr(mod, "__version__", None)
    if not ver:
        try:
            ver = md.version(dist or module)
        except Exception:
            ver = "present"
    return OK, ver


def _tool(name: str, arg: str = "--version"):
    path = shutil.which(name)
    if not path:
        return None, "not on PATH"
    try:
        out = subprocess.run([path, arg], capture_output=True, text=True, timeout=20)
        line = (out.stdout or out.stderr).strip().splitlines()
        return OK, f"{line[0] if line else 'found'}  ({path})"
    except Exception:
        return OK, f"found ({path})"


def _word_lists():
    found = []
    for p in DICT_PATHS:
        path = Path(p)
        if path.exists():
            n = sum(1 for line in path.open(errors="ignore") if line.strip())
            found.append(f"{p} ({n} words)")
    if found:
        return OK, "; ".join(found)
    return None, "no system word list found"


def _data_files():
    missing = [n for n in ("months.txt", "speech_verbs.txt", "function_words.txt")
               if not data_file(n).exists()]
    if missing:
        return None, "missing: " + ", ".join(missing)
    return OK, "months, speech verbs, function words"


# Font families Typst ships with, so they work without a system install.
TYPST_BUNDLED = {
    "libertinus serif", "libertinus math", "libertinus mono",
    "new computer modern", "new computer modern math",
    "dejavu sans", "dejavu serif", "dejavu sans mono",
}


def _font_in_pdf(family: str) -> bool | None:
    """Ask Typst itself: compile one line and look at the font it embeds."""
    try:
        import pymupdf
        import typst
    except ImportError:
        return None
    with tempfile.TemporaryDirectory() as td:
        src, out = Path(td) / "f.typ", Path(td) / "f.pdf"
        src.write_text(f'#set text(font: "{family}")\nAbcdefghij\n', encoding="utf-8")
        try:
            typst.compile(str(src), output=str(out))
        except Exception:
            return False
        doc = pymupdf.open(str(out))
        names = {x[3].split("+", 1)[-1].lower() for p in doc for x in p.get_fonts()}
        key = family.lower().replace(" ", "").replace("-", "")
        return any(n.startswith(key) for n in names)


def _fonts(root: Path):
    wanted = {"Libertinus Serif"}
    try:
        wanted |= {b.get("font") for b in load(root).values()}
    except ConfigError:
        pass
    wanted.discard(None)

    fc = shutil.which("fc-list")
    installed = set()
    if fc:
        try:
            out = subprocess.run([fc, "--format", "%{family}\n"], capture_output=True,
                                 text=True, timeout=30)
            installed = {l.strip().lower() for l in out.stdout.splitlines()}
        except Exception:
            fc = None

    good, note = [], []
    for family in sorted(wanted):
        key = family.lower()
        if key in installed:
            good.append(f"{family} (system)")
        elif _font_in_pdf(family):
            good.append(f"{family} (bundled with Typst)")
        elif key in TYPST_BUNDLED:
            good.append(f"{family} (bundled with Typst)")
        else:
            note.append(family)
    if not note:
        return OK, "; ".join(good)
    hint = "not in fontconfig and not bundled with Typst"
    if not fc:
        hint += " (and fontconfig is not installed, so system fonts cannot be probed)"
    return WARN, f"{hint}: {', '.join(note)} - Typst will substitute another serif; " \
                 "check page 1 of the PDF or set font = ... in books.toml"


def _books(root: Path):
    try:
        books = load(root)
    except ConfigError as e:
        return WARN, str(e)
    bad = [s for s, b in books.items() if not Path(b.cfg["pdf"]).exists()]
    if not books:
        return WARN, "no [book.<slug>] sections yet"
    if bad:
        return MISS, f"{len(books)} book(s), missing PDF for: " + ", ".join(bad)
    return OK, f"{len(books)} book(s), all PDFs present"


def check(root: Path) -> list[dict]:
    rows = []

    def add(name, status, value, affects, required=False):
        rows.append({"name": name, "status": status or MISS, "value": value,
                     "affects": affects, "required": required})

    py = platform.python_version()
    add("python", OK if sys.version_info >= (3, 11) else None, py,
        "books.toml needs tomllib", required=True)

    for name, mod, dist, affects in (
            ("PyMuPDF", "pymupdf", "pymupdf", "stage 1: reading the scan"),
            ("pillow", "PIL", "pillow", "drop-cap crops, EPUB cover"),
            ("numpy", "numpy", "numpy", "drop-cap ink detection")):
        status, value = _module(mod, dist)
        add(name, status, value, affects, required=True)

    status, value = _module("typst", "typst")
    add("typst (python)", status if status else WARN, value, "print PDF output")

    for name, affects in (("pandoc", "EPUB output"),
                          ("tesseract", "re-OCR of damaged drop caps")):
        status, value = _tool(name)
        add(name, status if status else WARN, value, affects)

    status, value = _word_lists()
    add("word lists", status if status else WARN, value, "spelling-aware repair rules")

    status, value = _data_files()
    add("bookpress data", status, value, "datelines, dialogue tags, glued words")

    status, value = _fonts(root)
    add("fonts", status, value, "print PDF typography")

    status, value = _books(root)
    add("books.toml", status, value, "which books you can convert")

    return rows


def report(root: Path, as_json: bool = False) -> tuple[int, list[dict]]:
    rows = check(root)
    if as_json:
        print(json.dumps(rows, indent=1))
        return (1 if any(r["required"] and r["status"] == MISS for r in rows) else 0), rows

    width = max(len(r["name"]) for r in rows)
    for r in rows:
        print(f"{MARK[r['status']]}  {r['name']:<{width}}  {r['value']}")
        if r["status"] != OK:
            print(f"   {'':<{width}}↳ {r['affects']}")
    print("\n✓ ready   ! works with reduced features   ✗ required piece missing")
    fatal = [r["name"] for r in rows if r["required"] and r["status"] == MISS]
    if fatal:
        print("install them with:  pip install -e .   (then re-run bookpress healthcheck)")
    return (1 if fatal else 0), rows
