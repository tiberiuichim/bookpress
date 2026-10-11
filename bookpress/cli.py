"""bookpress command line."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from . import __version__, clean as clean_stage, cover as cover_stage
from . import dropcaps as dropcaps_stage, extract as extract_stage
from . import render as render_stage, structure as structure_stage
from .config import Book, ConfigError, add_book, get_book, load


STAGES = ["extract", "structure", "dropcaps", "fixes", "clean", "cover", "render"]


def _books(args, root: Path) -> list[Book]:
    if getattr(args, "all", False):
        return list(load(root).values())
    if not getattr(args, "slug", None):
        raise ConfigError("name a book (slug) or pass --all")
    return [get_book(root, args.slug)]


# ---------------------------------------------------------------- stages
def run_extract(book: Book) -> None:
    book.ensure_dirs()
    extract_stage.extract(Path(book.cfg["pdf"]), book.geometry)


def run_structure(book: Book) -> None:
    structure_stage.structure(book)


def run_dropcaps(book: Book) -> None:
    dropcaps_stage.ocr(book)
    dropcaps_stage.apply(book)


def run_fixes(book: Book) -> None:
    dropcaps_stage.apply_block_repairs(book)


def run_clean(book: Book) -> None:
    clean_stage.clean(book)


def run_cover(book: Book) -> None:
    cover_stage.cover(book)


def run_render(book: Book) -> None:
    render_stage.render(book)


RUNNERS = {"extract": run_extract, "structure": run_structure, "dropcaps": run_dropcaps,
           "fixes": run_fixes, "clean": run_clean, "cover": run_cover, "render": run_render}


def run_stage(stage: str, args, root: Path) -> int:
    for book in _books(args, root):
        print(f"\n=== {book.slug}: {stage} ===")
        RUNNERS[stage](book)
    return 0


# ---------------------------------------------------------------- commands
def cmd_add(args, root: Path) -> int:
    fields = {"pdf": args.pdf, "title": args.title, "author": args.author,
              "series": args.series, "lang": args.lang, "page": args.page,
              "pt": args.pt, "font": args.font, "cover_page": args.cover_page}
    path = add_book(root, args.slug, fields)
    print(f"added [book.{args.slug}] to {path}")
    print(f"next: bookpress all {args.slug}")
    return 0


def cmd_list(args, root: Path) -> int:
    for slug, book in sorted(load(root).items()):
        pdf = "ok" if Path(book.cfg["pdf"]).exists() else "MISSING"
        print(f"{slug:<18} {book.title:<40} pdf:{pdf:<8} {book.cfg['pdf']}")
    return 0


def cmd_status(args, root: Path) -> int:
    for book in _books(args, root):
        done = {"extract": book.geometry.exists(),
                "structure": book.book_json.exists(),
                "dropcaps": book.dropcap_log.exists(),
                "clean": book.clean_json.exists(),
                "render": (book.pdf_dir / f"{book.title}.pdf").exists()}
        marks = " ".join(f"{k}:{'+' if v else '-'}" for k, v in done.items())
        print(f"{book.slug:<18} {marks}")
        if book.report.exists():
            rep = json.loads(book.report.read_text(encoding="utf-8"))
            print(f"{'':<18} chapters in report: {len(rep['headings'])}, "
                  f"footnotes: {len(rep['footnotes'])}, "
                  f"pages dropped: {len(rep['dropped_pages'])}")
        if book.dropcap_log.exists():
            log = json.loads(book.dropcap_log.read_text(encoding="utf-8"))
            bad = [l for l in log if l["how"] == "UNRESOLVED"]
            if bad:
                print(f"{'':<18} unresolved drop caps: {len(bad)} "
                      f"(see overrides/dropcaps.json)")
    return 0


def cmd_review(args, root: Path) -> int:
    for book in _books(args, root):
        print(f"--- {book.slug}: {book.changes_txt} ---")
        if book.changes_txt.exists():
            print(book.changes_txt.read_text(encoding="utf-8"))
        else:
            print("  (run the clean stage first)")
    return 0


def cmd_words(args, root: Path) -> int:
    for book in _books(args, root):
        clean_stage.unknown_words(book, args.limit)
    return 0


def cmd_bootstrap(args, root: Path) -> int:
    venv = root / ".venv"
    subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True)
    subprocess.run([str(venv / "bin", "pip"), "install", "-e", "."], check=True)
    print(f"\nvenv ready: {venv}\n"
          "external tools still needed: pandoc (EPUB), tesseract (drop-cap OCR), "
          "and a system word list such as /usr/share/dict/words (spelling rules).")
    return 0


# ---------------------------------------------------------------- parser
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="bookpress",
                                description="scanned+OCR'd book PDF -> EPUB + print PDF")
    p.add_argument("--version", action="version", version=f"bookpress {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("add", help="register a book in books.toml")
    a.add_argument("--pdf", required=True)
    a.add_argument("--slug", required=True)
    a.add_argument("--title")
    a.add_argument("--author", default="")
    a.add_argument("--series", default="")
    a.add_argument("--lang", default="en")
    a.add_argument("--page", default="a5")
    a.add_argument("--pt", type=int, default=11)
    a.add_argument("--font", default="Libertinus Serif")
    a.add_argument("--cover-page", type=int, default=1)
    a.set_defaults(func=cmd_add)

    sub.add_parser("list", help="list the books in books.toml").set_defaults(func=cmd_list)

    for stage in STAGES:
        s = sub.add_parser(stage, help=f"run the {stage} stage")
        s.add_argument("slug", nargs="?")
        s.add_argument("--all", action="store_true")
        s.set_defaults(func=lambda args, root, stage=stage: run_stage(stage, args, root))

    s = sub.add_parser("all", help="run every stage")
    s.add_argument("slug", nargs="?")
    s.add_argument("--all", action="store_true")
    s.set_defaults(func=lambda args, root: _run_all(args, root))

    for name, func in (("status", cmd_status), ("review", cmd_review)):
        s = sub.add_parser(name, help=f"{name} a book")
        s.add_argument("slug", nargs="?")
        s.add_argument("--all", action="store_true")
        s.set_defaults(func=func)

    s = sub.add_parser("words", help="list words the cleaner could not resolve")
    s.add_argument("slug", nargs="?")
    s.add_argument("--all", action="store_true")
    s.add_argument("--limit", type=int, default=40)
    s.set_defaults(func=cmd_words)

    sub.add_parser("bootstrap", help="create .venv and install bookpress into it") \
        .set_defaults(func=cmd_bootstrap)
    return p


def _run_all(args, root: Path) -> int:
    for stage in STAGES:
        run_stage(stage, args, root)
    return 0


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    root = Path.cwd()
    try:
        return args.func(args, root)
    except ConfigError as e:
        print(f"bookpress: {e}", file=sys.stderr)
        return 2
    except FileNotFoundError as e:
        print(f"bookpress: missing file: {e}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
