"""books.toml handling: one section per book, plus the per-book work directory."""
from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

DEFAULTS = {
    "lang": "en",
    "page": "a5",
    "pt": 11,
    "font": "Libertinus Serif",
    "cover_page": 1,
    "heading": r"^(CHAPTER|EPILOGUE|PROLOGUE|PART|BOOK|INTRODUCTION|AUTHOR[S]?'S? NOTE)"
               r"\s*[\d.IVX]*\s*$",
    "chapter": r"^CHAPTER\s*(\d+)\.?\s*$",
    "footnote_start": [r"^[*†]\s*\S", r"^[A-Za-z]{0,3}[.,]?\s?\d{2,4}\b"],
    "quote_substitute": "",        # force a char that stands in for a lost closing quote
    "open_quote_chars": ["£"],     # chars the OCR misreads as an opening quote
    "epigraph_attribution": r"(?:,\s*(?:Act|Scene|Book|Chorus|Finale|Overture)\b"
                            r"|\b(Act|Scene)\s+\d)",
}


class ConfigError(Exception):
    pass


@dataclass
class Book:
    slug: str
    cfg: dict
    root: Path

    @property
    def dir(self) -> Path:
        return self.root / "books" / self.slug

    @property
    def geometry(self) -> Path:
        return self.dir / "geometry.json"

    @property
    def book_json(self) -> Path:
        return self.dir / "book.json"

    @property
    def clean_json(self) -> Path:
        return self.dir / "book.clean.json"

    @property
    def report(self) -> Path:
        return self.dir / "report.json"

    @property
    def changes_tsv(self) -> Path:
        return self.dir / "changes.tsv"

    @property
    def changes_txt(self) -> Path:
        return self.dir / "changes.txt"

    @property
    def dropcap_ocr(self) -> Path:
        return self.dir / "dropcap-ocr.json"

    @property
    def dropcap_log(self) -> Path:
        return self.dir / "dropcap-log.json"

    @property
    def overrides(self) -> Path:
        return self.dir / "overrides"

    @property
    def epub_dir(self) -> Path:
        return self.dir / "epub"

    @property
    def pdf_dir(self) -> Path:
        return self.dir / "pdf"

    @property
    def title(self) -> str:
        return self.cfg.get("title", self.slug)

    def get(self, key: str):
        return self.cfg.get(key, DEFAULTS[key])

    def override(self, name: str) -> Path:
        """Optional hand-written repair file: overrides/<name>.json"""
        return self.overrides / f"{name}.json"

    def ensure_dirs(self) -> None:
        for p in (self.dir, self.overrides, self.epub_dir, self.pdf_dir):
            p.mkdir(parents=True, exist_ok=True)


def workspace(root: Path) -> Path:
    path = root / "books.toml"
    if not path.exists():
        raise ConfigError(
            "no books.toml - copy books.example.toml to books.toml, "
            "or run: bookpress add --pdf inbox/scan.pdf --slug mybook --title '...'")
    return path


def load(root: Path) -> dict[str, Book]:
    path = workspace(root)
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    books = {}
    for slug, cfg in (data.get("book") or {}).items():
        merged = {**DEFAULTS, **cfg}
        merged["title"] = merged.get("title", slug.replace("-", " ").title())
        merged["pdf"] = str((root / merged.get("pdf", "")).resolve())
        books[slug] = Book(slug=slug, cfg=merged, root=root)
    if not books:
        raise ConfigError(f"books.toml has no [book.<slug>] sections: {path}")
    return books


def get_book(root: Path, slug: str) -> Book:
    books = load(root)
    if slug not in books:
        raise ConfigError(f"unknown book {slug!r}; known: {', '.join(sorted(books))}")
    return books[slug]


def add_book(root: Path, slug: str, fields: dict) -> Path:
    """Append a [book.<slug>] section, keeping the rest of the file untouched."""
    path = root / "books.toml"
    if not path.exists():
        path.write_text("# bookpress book definitions\n", encoding="utf-8")
    if f"[book.{slug}]" in path.read_text(encoding="utf-8"):
        raise ConfigError(f"book {slug!r} already defined in books.toml")
    lines = [f"\n[book.{slug}]"]
    for key in ("pdf", "title", "author", "series", "lang", "page", "pt", "font",
                "cover_page"):
        if fields.get(key) not in (None, ""):
            value = fields[key]
            if isinstance(value, int):
                lines.append(f"{key} = {value}")
            else:
                escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
                lines.append(f'{key} = "{escaped}"')
    with path.open("a", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    return path
