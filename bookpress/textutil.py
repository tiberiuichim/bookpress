"""Small text helpers shared by the stages."""
from __future__ import annotations

import re
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path

WORD_RE = re.compile(r"[A-Za-zÀ-ÿ'’]+")

# System word lists used as a spelling vocabulary.  Missing files are fine:
# the book's own vocabulary is added on top of whatever is found.
DICT_PATHS = (
    "/usr/share/dict/british-english",
    "/usr/share/dict/american-english",
    "/usr/share/dict/words",
)


def data_file(name: str) -> Path:
    return Path(__file__).parent / "data" / name


def read_words(name: str) -> list[str]:
    """Whitespace-separated entries, '#' comments allowed (months, speech verbs)."""
    path = data_file(name)
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        out.extend(line.split("#", 1)[0].split())
    return out


@lru_cache(maxsize=1)
def dictionary() -> frozenset[str]:
    words: set[str] = set()
    for p in DICT_PATHS:
        try:
            words |= {w.strip().lower() for w in Path(p).read_text(errors="ignore").splitlines()
                      if w.strip()}
        except OSError:
            pass
    return frozenset(words)


def edit_distance(a: str, b: str, cap: int = 3) -> int:
    """Bounded Levenshtein: returns cap+1 as soon as the answer is known to be worse."""
    if abs(len(a) - len(b)) > cap:
        return cap + 1
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def clean_ws(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def diff_fragments(a: str, b: str, width: int = 45):
    """Compact word-level diff used by the audit trail: pairs of (before, after)."""
    aw, bw = a.split(), b.split()
    sm = SequenceMatcher(None, aw, bw, autojunk=False)
    out = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        before = " ".join(aw[max(0, i1 - 2):i2 + 2])
        after = " ".join(bw[max(0, j1 - 2):j2 + 2])
        out.append((before[:width * 2], after[:width * 2]))
    return out or [(a[:width], b[:width])]
