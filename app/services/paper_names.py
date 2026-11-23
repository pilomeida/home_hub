"""The name a filed paper goes by (PLAN step 12, Pedro 2026-11-23): `date · kind · what it is · who or item`.

One formatter for the whole Hub (W2). No accents (ç → c, ã → a, as a general rule), no character a file name cannot
hold; the date is the paper's own (yyyy-mm-dd), else the day it arrived. The file itself is kept as received (Q4);
its original name stays as where it came from."""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Optional

from app.services.names import fold

SEPARATOR = " · "
MAX_LENGTH = 150
WHAT_WORDS = 12                                           # "what it is" is a few words, never a paragraph
_UNSAFE = re.compile(r'[/\\:*?"<>|\x00-\x1f]')


def plain(text: Optional[str]) -> str:
    """Accents dropped (keeping the case), unsafe characters removed, spaces collapsed."""
    import unicodedata
    decomposed = unicodedata.normalize("NFKD", text or "")
    no_accents = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return " ".join(_UNSAFE.sub(" ", no_accents).split()).strip(" .,;-")


def filed_name(*, paper_date: Optional[str], received: datetime | date, kind: str, what: Optional[str],
               who: Optional[str]) -> str:
    day = paper_date if paper_date and re.fullmatch(r"\d{4}-\d{2}-\d{2}", paper_date) else received.strftime("%Y-%m-%d")
    head = [day, plain(kind)]
    tail = [p for p in (" ".join(plain(what).split()[:WHAT_WORDS]).rstrip(" ,;-—–"), plain(who)) if p]
    if len(tail) == 2 and fold(tail[1]) in fold(tail[0]):
        tail = tail[:1]                                   # "Worten invoice, dishwasher" needs no second "Dishwasher"
    name = SEPARATOR.join([p for p in head if p] + tail)
    if len(name) > MAX_LENGTH:
        keep = SEPARATOR.join([p for p in head if p])
        room = MAX_LENGTH - len(keep) - len(SEPARATOR)
        last = (SEPARATOR + tail[1]) if len(tail) == 2 else ""
        what_room = room - len(last)
        name = keep + SEPARATOR + _cut(tail[0], what_room) + last
        name = name[:MAX_LENGTH].rstrip(" ·")
    return name


def _cut(text: str, room: int) -> str:
    """At most `room` characters, cut at a word, never in the middle of one."""
    if len(text) <= room:
        return text
    if room <= 0:
        return ""
    head = text[:room + 1]
    cut = head.rsplit(" ", 1)[0] if " " in head else text[:room]   # one endless word: cut it where it must be
    return cut[:room].rstrip(" ,;-—–")



def name_for_guess(guess: dict, kind_label: str, received: datetime | date) -> str:
    """The filed name a guess points to: the person if the paper is about one, else its one item, else nothing."""
    items = guess.get("items") or []
    who = guess.get("who") or (items[0]["name"] if len(items) == 1 else None)
    return filed_name(paper_date=guess.get("paper_date"), received=received, kind=kind_label, what=guess.get("what"),
                      who=who)
