"""SAME header text from soft bits: grammar-constrained maximum likelihood plus voting.

The header ``ZCZC-ORG-EEE-PSSCCC-...+TTTT-JJJHHMM-LLLLLLLL-`` (47 CFR 11.31(c)) is so
structured that most positions allow only a few characters. Each character is decoded as
the allowed character whose bits best match the soft bits (bit 8 is ignored: 11.31(a)(1)
lets it be 0 or 1). Closed sets (originators, issue day/hour/minute) are decoded as whole
strings. Event and location codes, purge minutes and station characters snap to the usual
values when that only needs weak bits flipped. Whether a location code is followed by
another one (``-``) or by the purge time (``+``) is decided with an eight-character
look-ahead.

The three repetitions are combined two ways (NWS Instruction 10-1712 B.3): soft bits are
averaged (weighted by each burst's SNR) before decoding, and the individual decodes are
voted per character. Each candidate text is scored against the averaged soft bits; the
best well-formed one wins. Its probability of being exactly right is estimated from a
Gaussian model of the soft bits (``posterior``).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import numpy as np

from . import codes

SIGNS = np.array([[1.0 if (c >> i) & 1 else -1.0 for i in range(7)] for c in range(128)])   # (128, 7)


def _cls(chars: str) -> np.ndarray:
    return np.array([ord(c) for c in chars])


DIGITS = _cls("0123456789")
UPPER = _cls("ABCDEFGHIJKLMNOPQRSTUVWXYZ")
STATION = np.array([c for c in range(0x20, 0x7F) if chr(c) not in "-+"])
STATION_USUAL = _cls("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789/ ")
SEPARATORS = _cls("-+")
SNAP_BITS = 0.6           # snap to a usual value if the bits to flip are worth < 0.6 typical bits
MAX_LOCATIONS = 31
MAX_KAPPA = 60.0
KAPPA_SCALE = 0.75        # tempers the Gaussian posterior: in 48 trials at -2..+1 dB no wrong header scored >= 0.8
HEADER_RE = re.compile(r"^ZCZC-([A-Z]{3})-([A-Z]{3})-((?:\d{6}-){0,30}\d{6})\+(\d{4})-(\d{3})(\d{2})(\d{2})-([^-+]{8})-$")


def _strings(items) -> np.ndarray:
    return np.array([[ord(c) for c in s] for s in items])


ORG_OPTIONS = _strings(sorted(codes.ORIGINATORS))
EVENT_OPTIONS = _strings(sorted(codes.EVENTS))
JJJ_OPTIONS = _strings(f"{d:03d}" for d in range(1, 367))
HH_OPTIONS = _strings(f"{h:02d}" for h in range(24))
MM_OPTIONS = _strings(f"{m:02d}" for m in range(60))
PURGE_MM_OPTIONS = _strings(("00", "15", "30", "45"))   # 11.31(c): 15- or 30-minute steps
_LOCATION_OPTIONS: np.ndarray | None = None


def location_options() -> np.ndarray:
    """Every known SSCCC as an (n, 5) array of character codes."""
    global _LOCATION_OPTIONS
    if _LOCATION_OPTIONS is None:
        _LOCATION_OPTIONS = _strings(codes.known_location_codes())
    return _LOCATION_OPTIONS


def char_soft(soft: np.ndarray, n_chars: int) -> np.ndarray:
    """(n_chars, 7) soft values per character (bit 8 dropped), zero-padded."""
    v = np.zeros(n_chars * 8)
    m = min(len(soft), len(v))
    v[:m] = soft[:m]
    return v.reshape(n_chars, 8)[:, :7]


class _Cursor:
    """Left-to-right decoder over per-character soft values."""

    def __init__(self, v: np.ndarray):
        self.scores = v @ SIGNS.T                  # (n, 128): agreement of each character with the bits
        self.mag = np.abs(v).sum(axis=1) / 7.0     # typical bit magnitude per character
        self.n = len(v)
        self.chars: list[int] = []
        self.snapped: list[str] = []

    @property
    def i(self) -> int:
        return len(self.chars)

    def s(self, i: int, c: int) -> float:
        return float(self.scores[i, c]) if i < self.n else 0.0

    def best(self, i: int, cls: np.ndarray) -> tuple[int, float]:
        if i >= self.n:
            return int(cls[0]), 0.0
        j = int(np.argmax(self.scores[i, cls]))
        return int(cls[j]), float(self.scores[i, cls[j]])

    def take(self, chars) -> None:
        self.chars.extend([int(c) for c in chars])

    def fixed(self, text: str) -> None:
        self.take([ord(c) for c in text])

    def classes(self, cls: np.ndarray, n: int) -> None:
        i = self.i
        self.take([self.best(i + k, cls)[0] for k in range(n)])

    def closed(self, options: np.ndarray) -> None:
        """The best of a closed set of strings (options: (m, k) character codes)."""
        self.take(options[int(np.argmax(self.string_scores(self.i, options)))])

    def semi_closed(self, options: np.ndarray, cls: np.ndarray, label: str) -> None:
        """The best known string, unless reaching it would flip bits the decoder is sure about."""
        k, i = options.shape[1], self.i
        free = [self.best(i + j, cls) for j in range(k)]
        sc = self.string_scores(i, options)
        j = int(np.argmax(sc))
        typical = float(np.mean(self.mag[i:i + k])) if i < self.n else 0.0
        if (sum(s for _, s in free) - sc[j]) / 2.0 <= SNAP_BITS * typical:
            if any(c != o for (c, _), o in zip(free, options[j])):
                self.snapped.append(label)
            self.take(options[j])
        else:
            self.take([c for c, _ in free])

    def usual_chars(self, usual: np.ndarray, cls: np.ndarray, n: int) -> None:
        """Per character: the best usual character if it only needs weak bits flipped."""
        i = self.i
        out = []
        for k in range(n):
            (cu, su), (cf, sf) = self.best(i + k, usual), self.best(i + k, cls)
            weak = (sf - su) / 2.0 <= SNAP_BITS * (self.mag[i + k] if i + k < self.n else 0.0)
            out.append(cu if weak else cf)
        self.take(out)

    def string_scores(self, i: int, options: np.ndarray) -> np.ndarray:
        idx = i + np.arange(options.shape[1])
        ok = idx < self.n
        if not ok.any():
            return np.zeros(len(options))
        return self.scores[idx[ok][None, :], options[:, ok]].sum(axis=1)

    def plus_next(self) -> bool:
        """After a location code: True if '+' (purge time follows) beats '-' (another code)."""
        i = self.i
        d = [self.best(i + k, DIGITS)[1] for k in range(1, 8)]
        plus = self.s(i, ord("+")) + sum(d[0:4]) + self.s(i + 5, ord("-")) + self.best(i + 6, DIGITS[:4])[1] + d[6]
        minus = self.s(i, ord("-")) + sum(d[0:6]) + max(self.s(i + 7, ord("-")), self.s(i + 7, ord("+")))
        return plus >= minus

    def text(self) -> str:
        return "".join(chr(c) for c in self.chars)


def decode_header(soft: np.ndarray) -> tuple[str, list[str]]:
    """Most likely header text for the soft bits of one payload (from the first 'Z'),
    and the fields that were snapped to a known code."""
    cur = _Cursor(char_soft(soft, min(len(soft) // 8 + 1, 260)))
    cur.fixed("ZCZC-")
    cur.closed(ORG_OPTIONS)
    cur.fixed("-")
    cur.semi_closed(EVENT_OPTIONS, UPPER, "event")
    cur.fixed("-")
    for k in range(MAX_LOCATIONS):
        cur.classes(DIGITS, 1)                         # P: part of the county
        cur.semi_closed(location_options(), DIGITS, f"location {k + 1}")
        if k == MAX_LOCATIONS - 1 or cur.plus_next():
            cur.fixed("+")
            break
        cur.fixed("-")
    cur.classes(DIGITS, 2)
    cur.semi_closed(PURGE_MM_OPTIONS, DIGITS, "purge minutes")
    cur.fixed("-")
    cur.closed(JJJ_OPTIONS)
    cur.closed(HH_OPTIONS)
    cur.closed(MM_OPTIONS)
    cur.fixed("-")
    cur.usual_chars(STATION_USUAL, STATION, 8)
    cur.fixed("-")
    return cur.text(), cur.snapped


def parse(text: str) -> dict | None:
    """Fields of a well-formed header, or None."""
    m = HEADER_RE.match(text)
    if not m:
        return None
    org, event, locs, purge, jjj, hh, mm, station = m.groups()
    j, h, mi = int(jjj), int(hh), int(mm)
    if org not in codes.ORIGINATORS or not (1 <= j <= 366 and h < 24 and mi < 60):
        return None
    return {"originator": org, "event": event, "locations": locs.split("-"),
            "purge_minutes": int(purge[:2]) * 60 + int(purge[2:]), "jjj": j, "hh": h, "mm": mi,
            "station": station}


def score_text(soft: np.ndarray, text: str) -> np.ndarray:
    """Per character: mean soft agreement of its 7 bits with the text (~1 clean, ~0 noise)."""
    v = char_soft(soft, len(text))
    c = np.array([ord(ch) & 0x7F for ch in text])
    return np.einsum("ij,ij->i", v, SIGNS[c]) / 7.0


def _decisions(fields: dict) -> list[tuple[int, np.ndarray]]:
    """(start position, options) for every choice the decoder made for this header layout.
    Options are (m, k) arrays of character codes; fixed characters are not choices."""
    out: list[tuple[int, np.ndarray]] = [(5, ORG_OPTIONS)]
    ev = fields["event"]
    out += [(9, EVENT_OPTIONS)] if ev in codes.EVENTS else [(9 + k, UPPER[:, None]) for k in range(3)]
    known = set(codes.known_location_codes())
    pos = 13
    for loc in fields["locations"]:
        out.append((pos, DIGITS[:, None]))
        out += [(pos + 1, location_options())] if loc[1:] in known else [(pos + 1 + k, DIGITS[:, None]) for k in range(5)]
        out.append((pos + 6, SEPARATORS[:, None]))
        pos += 7
    out += [(pos, DIGITS[:, None]), (pos + 1, DIGITS[:, None]), (pos + 2, _strings(f"{m:02d}" for m in range(100)))]
    out += [(pos + 5, JJJ_OPTIONS), (pos + 8, HH_OPTIONS), (pos + 10, MM_OPTIONS)]
    out += [(pos + 13 + k, STATION[:, None]) for k in range(8)]
    return out


def posterior(soft: np.ndarray, text: str, fields: dict) -> tuple[float, float]:
    """(probability that every character of ``text`` is right, kappa).

    Model: soft bit = ±A + Gaussian noise of variance sigma^2, so log P(choice) is
    kappa * agreement + const with kappa = A / sigma^2, estimated from the decoded bits.
    """
    v = char_soft(soft, len(text))
    beta = SIGNS[np.array([ord(c) for c in text])]
    u = (v * beta).ravel()
    a, var = float(np.mean(u)), float(np.var(u))
    kappa = float(np.clip(a / max(var, 1e-6), 0.0, MAX_KAPPA)) * KAPPA_SCALE
    scores = v @ SIGNS.T
    logp = 0.0
    for start, options in _decisions(fields):
        k = options.shape[1]
        if start + k > len(text):
            continue
        sc = scores[start + np.arange(k)[None, :], options].sum(axis=1) * kappa
        chosen = float(scores[start + np.arange(k), [ord(c) for c in text[start:start + k]]].sum()) * kappa
        logp += chosen - (np.max(sc) + np.log(np.sum(np.exp(sc - np.max(sc)))))
    return float(np.exp(min(logp, 0.0))), kappa


def majority(texts: list[str]) -> str | None:
    """Per-character majority of the texts that share the most common length (None if < 2)."""
    lengths = [len(t) for t in texts]
    if len(texts) < 2:
        return None
    n = max(set(lengths), key=lengths.count)
    same = [t for t in texts if len(t) == n]
    if len(same) < 2:
        return None
    return "".join(max(set(chars), key=chars.count) for chars in zip(*same))


@dataclass
class Combined:
    text: str
    fields: dict
    q: np.ndarray                   # per-character agreement with the combined soft bits
    p_correct: float                # posterior probability that the whole text is right
    burst_texts: list[str]
    n_agree: int                    # bursts whose own decode equals the final text
    snapped: list[str] = field(default_factory=list)
    method: str = "soft-combined"   # "soft-combined", "vote" or "single burst"

    @property
    def quality(self) -> float:
        return float(np.mean(self.q)) if len(self.q) else 0.0


def combine(softs: list[np.ndarray], weights: list[float]) -> Combined | None:
    """Decode a group of repetitions of one header. None if nothing well-formed comes out."""
    n = max(len(s) for s in softs)
    acc, wsum = np.zeros(n), np.zeros(n)
    for s, w in zip(softs, weights):
        acc[: len(s)] += max(w, 1e-6) * s
        wsum[: len(s)] += max(w, 1e-6)
    avg = acc / np.maximum(wsum, 1e-12)
    singles = [decode_header(s)[0] for s in softs] if len(softs) > 1 else []
    joint, snapped = decode_header(avg)
    cands = {joint: "soft-combined"}
    vote = majority(singles)
    if vote:
        cands.setdefault(vote, "vote")
    for t in singles:
        cands.setdefault(t, "single burst")
    best = None
    for text, method in cands.items():
        fields = parse(text)
        if fields is not None:
            q = score_text(avg, text)
            if best is None or q.sum() > best[2].sum():
                best = (text, fields, q, method)
    if best is None:
        return None
    text, fields, q, method = best
    p, _ = posterior(avg, text, fields)
    return Combined(text=text, fields=fields, q=q, p_correct=p, burst_texts=singles or [joint],
                    n_agree=sum(t == text for t in singles) if singles else 1,
                    snapped=snapped if text == joint else [], method=method if len(softs) > 1 else "single burst")
