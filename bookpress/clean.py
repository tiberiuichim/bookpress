"""Stage 4 - OCR cleanup.  Every single change is logged so it can be reviewed.

Reads  books/<slug>/book.json
Writes books/<slug>/book.clean.json
       books/<slug>/changes.tsv      rule, page, chapter, before, after
       books/<slug>/changes.txt      human-readable summary grouped by rule

Rules are deliberately conservative: a word is only rewritten when the
replacement is far more common in this book than the form found in the scan,
and quote repair only fires where the paragraph is already clearly dialogue.

Optional hand-written fixes: books/<slug>/overrides/words.json
      [{"find": "survning", "replace": "surviving"}, ...]
"""
from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

from .textutil import (clean_ws, dictionary, diff_fragments, edit_distance, read_words,
                       WORD_RE)

TOKEN_KEEP = set(".,!?;:‘’“”\"()-–—…*/&")
TERMINAL = (".", "!", "?", "…", "”", "’", '"', ")")
SPEECH_VERBS = read_words("speech_verbs.txt")
FUNCTION_WORDS = set(read_words("function_words.txt"))
SPEECH_TAIL = re.compile(
    r"(?<=[,.!?…])\s+(?:[A-Za-zÀ-ÿ]+\s+){0,3}(?:" + "|".join(SPEECH_VERBS) + r")"
    r"\b(?:\s+[A-Za-zÀ-ÿ’]+){0,4}[.!?…]$")
OPEN_QUOTE = re.compile(r"[‘“]")
CLOSE_QUOTE = re.compile(r"[’”]")


def _quote_like(s: str, i: int) -> bool:
    """A quote glyph sandwiched between letters is an apostrophe, not a quote."""
    prev = s[i - 1:i]
    nxt = s[i + 1:i + 2]
    return not (prev.isalpha() and nxt.isalpha())


class Cleaner:
    def __init__(self, book):
        self.book = book
        self.changes = []
        self.vocab = {self._key(w) for w in dictionary()}
        self.freq: Counter = Counter()
        self.freq_cap: Counter = Counter()
        for ch in book["chapters"]:
            for b in ch["blocks"]:
                self._tally(b["text"])
                for fn in b.get("footnotes", []):
                    self._tally(fn)
        self.vocab |= {w for w, c in self.freq.items() if c >= 3} | {"a", "i"}
        self.quote_sub = self._detect_quote_substitute(book)
        self.open_quote_chars = book.get("open_quote_chars") or []

    @staticmethod
    def _key(w: str) -> str:
        """Vocabulary keys use the ASCII apostrophe (dictionary lists do too)."""
        return w.lower().replace("\u2019", "'")

    def _tally(self, text):
        for w in WORD_RE.findall(text):
            self.freq[self._key(w)] += 1
            if w[:1].isupper():
                self.freq_cap[self._key(w)] += 1

    def _detect_quote_substitute(self, book) -> str:
        """Some scans render the closing quote as a digit.  If one digit keeps
        appearing exactly where a closing quote belongs, treat it as one."""
        forced = self.book.get("quote_substitute")
        if forced:
            return forced[:1]
        counts = Counter()
        for ch in book["chapters"]:
            for b in ch["blocks"]:
                for d in "0123456789":
                    counts[d] += len(re.findall(rf"[,.!?…]{d}(?=[\s”’)]|$)", b["text"]))
        if not counts:
            return ""
        digit, n = counts.most_common(1)[0]
        return digit if n >= 20 else ""

    def log(self, rule, page, chnum, before, after):
        if before != after:
            self.changes.append({"rule": rule, "page": page, "chapter": chnum,
                                 "before": before, "after": after})

    # ---- rules -----------------------------------------------------------
    def junk_tokens(self, t, page, chnum):
        out = []
        for tok in t.split():
            core = tok.strip("‘’“”\"'().,;:!?-–—")
            if (not WORD_RE.findall(tok) and tok not in TOKEN_KEEP
                    and not re.search(r"\d", tok)):
                continue                        # stray symbol: \ | ■ ▼ ¢ () (+)
            if (len(core) == 1 and core.isalpha() and core not in "aAiI"
                    and re.search(r"[a-z]{4}", t) and "." not in tok
                    and re.search(r"\s" + re.escape(tok) + r"\s[a-z]", " " + t + " ")):
                continue                        # stray single letter before lowercase
            out.append(tok)
        return " ".join(out)

    def punctuation(self, t, page, chnum):
        if self.quote_sub:
            t = re.sub(rf"(?<=[A-Za-z,.!?…]){self.quote_sub}(?=[\s”’)]|$)", "’", t)
        t = re.sub(r"\s+([,.;:!?])", r"\1", t)                    # space before punct
        t = re.sub(r"([,.;:!?])(?=[A-Za-zÀ-ÿ‘“(])", r"\1 ", t)     # missing space after
        t = re.sub(r"(?<!\d)[.]\s*[.]\s*[.](?!\.)", "…", t)       # '. . .' -> ellipsis
        t = t.replace("...", "…")
        t = re.sub(r"‘{2,}", "‘", t)
        t = re.sub(r"’{2,}", "’", t)
        t = re.sub(r"\s{2,}", " ", t)
        t = re.sub(r"(?<=[A-Za-z,.!?…])=(?=\s)", "", t)            # lone '=' OCR slip
        for ch in self.open_quote_chars:                          # misread opener
            t = re.sub(re.escape(ch) + r"(?=[A-ZÀ-Þ])", "‘", t)
        t = re.sub(r"(\d)\)", r"\1", t)                           # '1)' noise after a digit
        # straight quotes -> typographic quotes
        t = re.sub(r"(^|[\s(—–])['‘](?=[A-Za-zÀ-ÿ])", r"\1‘", t)
        t = re.sub(r"(?<=[A-Za-zÀ-ÿ])['‘](?=$|[\s.,;:!?)’”])", "’", t)
        t = re.sub(r"(?<=[A-Za-zÀ-ÿ])['‘](?=[A-Za-zÀ-ÿ])", "’", t)     # don't -> don’t
        t = re.sub(r"[’']\*[’']", "*", t)                          # '*'' footnote marker
        t = re.sub(r"(?<=[A-Za-z])’{2,3}(?=[.,;:!?]\s)", "*", t)   # ''' -> footnote marker
        if "‘" not in t and "“" not in t:
            t = re.sub(r"(?<=[.,!?…’”])[’'](?=\s|$)", "", t)        # stray trailing quote
        t = re.sub(r"(^|[\s(—–])\"(?=[A-Za-zÀ-ÿ])", r"\1“", t)
        t = re.sub(r"(?<=[A-Za-zÀ-ÿ])\"(?=$|[\s.,;:!?)])", "”", t)
        # a closing quote that the scan put after the comma:  paper,"  ->  paper,”
        t = re.sub(r'(?<=[,.;:!?…])"(?=\s|$)', "”", t)
        return t

    def split_glued(self, t, page, chnum):
        def repl(m):
            w = m.group(0)
            if re.match(r"^[a-z]+[A-Z][a-z]+$", w):               # JosefAnton -> Josef Anton
                return re.sub(r"(?<=[a-z])(?=[A-Z])", " ", w)
            low = w.lower()
            if low in self.vocab or len(w) < 6:
                return w
            for i in range(1, len(w) - 1):                        # ofThoth -> of Thoth
                a, b = w[:i], w[i:]
                if (a.lower() in FUNCTION_WORDS and self.freq[a.lower()] >= 5
                        and self.freq[b.lower()] >= 3 and b.lower() in self.vocab):
                    return a + " " + b
            return w
        return re.sub(r"[A-Za-zÀ-ÿ]+", repl, t)

    def fix_words(self, t, page, chnum):
        def repl(m):
            w = m.group(0)
            low = self._key(w)
            if low in self.vocab or len(low) < 3 or w.isupper() or w[:1].isdigit():
                return w
            cap = 1 if len(low) <= 6 else 2
            cands = []
            for v in self.vocab:
                if abs(len(v) - len(low)) > cap:
                    continue
                d = edit_distance(low, v, cap)
                if d and d <= cap:
                    cands.append((d, self.freq[v], v))
            if not cands:
                return w
            cands.sort(key=lambda c: (c[0], -c[1]))
            best = cands[0]
            here = self.freq[low]
            # only auto-correct when the scanned form is rare and the fix is common
            if here > 3 or best[1] < 10 * max(here, 1) \
                    or (len(cands) > 1 and best[0] == cands[1][0]):
                return w
            out = best[2]
            never_lower = self.freq[best[2]] <= self.freq_cap[best[2]]
            if w[0].isupper() or never_lower:
                out = out[0].upper() + out[1:]          # proper noun / was capitalised
            self.log("word", page, chnum, w, out)
            return out
        return re.sub(r"[A-Za-zÀ-ÿ]+", repl, t)

    def quotes(self, t, page, chnum):
        """Dialogue paragraphs open with ‘ and close with ’.  Repair the three
        ways scans break that, one paragraph at a time."""
        ts = t.lstrip()
        if (ts.startswith(("’", "”")) and "‘" not in t and "“" not in t
                and ts[1:2].isalpha() and ts.rstrip()[-1:] in ".!?…"):
            t = t.replace(ts[0], "‘", 1).rstrip() + "’"
            self.log("quote", page, chnum, "(flipped opener)", t[:70])
        if (t.rstrip().endswith(("’", "”")) and not t.lstrip().startswith(("‘", "“", '"'))
                and "‘" not in t and "“" not in t and WORD_RE.findall(t[:3])):
            t = "‘" + t
            self.log("quote", page, chnum, "(no opener)", t[:70])
        # a closing quote lost just before the speech tag:
        #   ‘Nothing at all, lamented X.   ->   ‘Nothing at all,’ lamented X.
        ts = t.strip()
        if ts[:1] in ("‘", "“") and ts[-1:] in ".!?…":
            last_open = max((m.start() for m in OPEN_QUOTE.finditer(ts)
                             if _quote_like(ts, m.start())), default=-1)
            tail = ts[last_open + 1:]
            if last_open >= 0 and not any(_quote_like(tail, m.start())
                                          for m in CLOSE_QUOTE.finditer(tail)):
                m = SPEECH_TAIL.search(ts)
                if m:
                    at = m.start()
                    ts = (ts[:at] + "’" + ts[at:] if ts[at - 1] in ",.!?…"
                          else ts[:at].rstrip() + "’" + ts[at:])
                    self.log("quote", page, chnum, ts[max(0, at - 30):at + 20], ts[:70])
                    t = ts
        return t

    def markers(self, t, fns, page, chnum):
        """Orphaned footnote markers: a leading *, or a digit glued to the
        punctuation it follows (the scan lost the superscript)."""
        n = len(fns or ())
        # the marker and its footnote can sit in different paragraphs
        for pat in (r'(?<=[,;:])"', r"(?<=[.,;:!?…])\d{1,3}(?=\s+[A-Z])"):
            t2 = re.sub(pat, "", t)
            if t2 != t:
                self.log("marker", page, chnum, t[:40], t2[:40])
                t = t2
        t2 = re.sub(r"^[*†]\s*", "", t)
        if t2 != t:
            self.log("marker", page, chnum, t[:40], t2[:40])
            t = t2
        for _ in range(n):
            found = list(re.finditer(r'(?<=[A-Za-z…’”.,;:!?])([*†"]|\d{1,3})(?=\s|$)', t))
            if not found:
                break
            x = found[-1]
            t = t[:x.start()] + t[x.end():]
            self.log("marker", page, chnum, x.group(0), "(removed)")
        return t

    def promote_footnotes(self, book):
        """A body paragraph opening with '* ' (marker + space) is footnote text
        the page classifier missed: attach it to the paragraph above."""
        for ch in book["chapters"]:
            keep = []
            for b in ch["blocks"]:
                if (b["type"] == "para" and re.match(r"^[*†]\s", b["text"])
                        and len(b["text"]) < 500 and keep and keep[-1]["type"] == "para"):
                    prev = keep[-1]
                    prev.setdefault("footnotes", []).append(b["text"].lstrip("*† "))
                    self.log("footnote", b["page"], ch["number"], b["text"][:60], "(promoted)")
                    continue
                keep.append(b)
            ch["blocks"] = keep

    # ---- driver ----------------------------------------------------------
    def run(self, fixes=()):
        self.promote_footnotes(self.book)
        for ch in self.book["chapters"]:
            for b in ch["blocks"]:
                for fx in fixes:
                    if fx["find"] in b["text"]:
                        b["text"] = b["text"].replace(fx["find"], fx["replace"])
                        self.log("manual-word", b["page"], ch["number"],
                                 fx["find"], fx["replace"])
                t0 = b["text"]
                t = self.punctuation(t0, b["page"], ch["number"])
                t = self.junk_tokens(t, b["page"], ch["number"])
                t = self.split_glued(t, b["page"], ch["number"])
                t = self.fix_words(t, b["page"], ch["number"])
                if b["type"] == "para":
                    t = self.quotes(t, b["page"], ch["number"])
                    t = self.markers(t, b.get("footnotes"), b["page"], ch["number"])
                if t != t0:
                    for a, b2 in diff_fragments(t0, t):
                        self.log("para", b["page"], ch["number"], a, b2)
                b["text"] = t
                if b.get("footnotes"):
                    b["footnotes"] = [self.punctuation(f, b["page"], ch["number"])
                                      .lstrip("*† ") for f in b["footnotes"]]
        for f in self.book.get("front", []):
            for blk in f["blocks"]:
                t = self.punctuation(blk["text"], f["page"], None)
                t = self.junk_tokens(t, f["page"], None)
                t = self.fix_words(t, f["page"], None)
                blk["text"] = t
        return self.book


def clean(book) -> dict:
    book_json = json.loads(book.book_json.read_text(encoding="utf-8"))
    fixes_path = book.override("words")
    fixes = json.loads(fixes_path.read_text(encoding="utf-8")) if fixes_path.exists() else ()
    c = Cleaner(book_json)
    c.run(fixes)
    book.ensure_dirs()
    book.clean_json.write_text(json.dumps(book_json, indent=1), encoding="utf-8")

    with book.changes_tsv.open("w", encoding="utf-8") as fh:
        for ch in c.changes:
            fh.write(f"{ch['rule']}\t{ch['page']}\t{ch['chapter']}\t"
                     f"{ch['before']}\t{ch['after']}\n")
    by_rule = Counter(ch["rule"] for ch in c.changes)
    with book.changes_txt.open("w", encoding="utf-8") as fh:
        fh.write(f"{book.slug}: {len(c.changes)} changes {dict(by_rule)}\n")
        if c.quote_sub:
            fh.write(f"closing quote rendered as {c.quote_sub!r} in this scan\n")
        fh.write("\n")
        # every rule that fired, canonical order first, then the rest by frequency
        order = ([r for r in ("word", "quote", "footnote", "marker", "para") if r in by_rule]
                 + [r for r in by_rule if r not in ("word", "quote", "footnote", "marker",
                                                    "para")])
        for rule in order:
            items = [ch for ch in c.changes if ch["rule"] == rule]
            fh.write(f"--- {rule} ({len(items)}) ---\n")
            for ch in items[:200]:
                fh.write(f"p{ch['page']:>4} ch{str(ch['chapter']):>3} "
                         f"{ch['before'][:60]!r} -> {ch['after'][:60]!r}\n")
            if len(items) > 200:
                fh.write(f"... {len(items) - 200} more (see {book.changes_tsv.name})\n")
            fh.write("\n")
    print(f"{book.slug}: {len(c.changes)} changes {dict(by_rule)}")
    return book_json


def unknown_words(book, limit: int = 40):
    """Words still outside the dictionary + book vocabulary, with context.
    This is the list to skim when deciding what to add to overrides/words.json."""
    book_json = json.loads(book.clean_json.read_text(encoding="utf-8"))
    c = Cleaner(book_json)
    cnt, ctx = Counter(), {}
    for ch in book_json["chapters"]:
        for b in ch["blocks"]:
            for sent in re.split(r"(?<=[.!?…])\s+", b["text"]):
                for w in WORD_RE.findall(sent):
                    low = Cleaner._key(w)
                    if low in c.vocab or len(low) < 3 or w.isupper():
                        continue
                    cnt[low] += 1
                    ctx.setdefault(low, (b["page"], sent[:90]))
    for w, n in cnt.most_common(limit):
        p, s = ctx[w]
        print(f"{n:>3} {w:<18} p{p:<4} {s!r}")
    return cnt
