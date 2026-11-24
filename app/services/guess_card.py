"""What the Hub read, on the Inbox card (PLAN step 12c): the guess shown for Pedro to edit, his edits read back and
checked like the AI's own answer (Q1), and what is new (a room, a person) created when he approves.

Who comes from the family (S5), rooms from the room list; anything else is shown as new and only created on
approval. Item rows belong to areas whose papers list physical things (`CategorySpec.keeps_items`: House). The name
the paper will carry is built by the one formatter (W2, app/services/paper_names.py)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Mapping, Optional, Sequence

from sqlmodel import Session

from app.models.subject import SubjectKind
from app.services import household, subjects
from app.services.category_classifier import checked_guess
from app.services.names import name_key
from app.services.paper_names import name_for_guess

WARRANTY_YEARS = 3                       # Pedro, 2026-11-23: an item's warranty ends 3 years after its purchase
PREFIX = "g_"                            # every form key of this block starts with it; never a paper field


def warranty_end(paper_date: Optional[str]) -> str:
    if not paper_date:
        return ""
    try:
        bought = date.fromisoformat(paper_date)
    except ValueError:
        return ""
    try:
        return bought.replace(year=bought.year + WARRANTY_YEARS).isoformat()
    except ValueError:                   # bought on 29 February
        return bought.replace(year=bought.year + WARRANTY_YEARS, day=28).isoformat()


@dataclass
class ItemRow:
    name: str
    room: str
    warranty_end: str
    new_room: bool


@dataclass
class GuessView:
    who: str
    new_person: bool
    paper_date: str
    what: str
    tags: str
    keeps_items: bool
    items: list[ItemRow] = field(default_factory=list)
    filed_name: str = ""
    family: list[str] = field(default_factory=list)
    rooms: list[str] = field(default_factory=list)


def _known(name: str, names: Sequence[str]) -> bool:
    return bool(name) and name_key(name) in {name_key(n) for n in names}


def view(session: Session, guess: Mapping, *, keeps_items: bool, kind_label: str, received: datetime) -> GuessView:
    family = [s.name for s in household.family(session)]
    rooms = [s.name for s in household.rooms(session)]
    paper_date = guess.get("paper_date") or ""
    rows = [ItemRow(name=i.get("name") or "", room=i.get("room") or "",
                    warranty_end=i.get("warranty_end") or warranty_end(paper_date),
                    new_room=bool(i.get("room")) and not _known(i.get("room") or "", rooms))
            for i in (guess.get("items") or [])] if keeps_items else []
    who = guess.get("who") or ""
    return GuessView(who=who, new_person=bool(who) and not _known(who, family), paper_date=paper_date,
                     what=guess.get("what") or "", tags=", ".join(guess.get("tags") or []), keeps_items=keeps_items,
                     items=rows, filed_name=name_for_guess(dict(guess, items=[vars(r) for r in rows]), kind_label,
                                                           received),
                     family=family, rooms=rooms)


def field_values(guess: Mapping, from_guess: Mapping[str, str]) -> dict[str, str]:
    """The kind's own fields the guess already answers (PLAN step 12d, `CategorySpec.from_guess`); empty ones left
    out, so the card still asks them."""
    items = [i for i in (guess.get("items") or []) if (i.get("name") or "").strip()]
    rooms = {i.get("room") for i in items if i.get("room")}
    parts = {
        "who": guess.get("who") or "",
        "paper_date": guess.get("paper_date") or "",
        "what": guess.get("what") or "",
        "item": items[0]["name"] if len(items) == 1 else (guess.get("what") or "" if items else ""),
        "room": next(iter(rooms)) if len(rooms) == 1 else "",
    }
    return {key: parts.get(part, "") for key, part in from_guess.items() if parts.get(part, "")}


def blank_row() -> ItemRow:
    return ItemRow(name="", room="", warranty_end="", new_room=False)


def from_form(session: Session, form, guess: Mapping, *, keeps_items: bool) -> dict:
    """Pedro's edits as a guess, checked like the AI's (Q1). `form` is the request's form (repeated item keys)."""
    if f"{PREFIX}what" not in form:      # the block was not on the page: the guess stays as it was
        return dict(guess)
    getlist = getattr(form, "getlist", lambda key: [form[key]] if key in form else [])
    names, rooms_typed, ends = (getlist(f"{PREFIX}item_name"), getlist(f"{PREFIX}item_room"),
                                getlist(f"{PREFIX}item_warranty"))
    data = {"who": form.get(f"{PREFIX}who", ""), "paper_date": form.get(f"{PREFIX}paper_date", ""),
            "what": form.get(f"{PREFIX}what", ""),
            "tags": [t for t in (form.get(f"{PREFIX}tags", "") or "").split(",")],
            "items": [{"name": n, "room": r} for n, r in zip(names, rooms_typed + [""] * len(names))] if keeps_items else [],
            "act_by": guess.get("act_by")}
    family = [s.name for s in household.family(session)]
    rooms = [s.name for s in household.rooms(session)]
    checked = checked_guess(data, family, rooms)
    for row, end in zip(checked["items"], [e for n, e in zip(names, ends + [""] * len(names)) if (n or "").strip()]):
        row["warranty_end"] = _day(end) or warranty_end(checked["paper_date"])
    keep = {k: guess[k] for k in ("new_area", "new_kind") if guess.get(k)}
    return {**keep, **checked}


def _day(value: str) -> str:
    try:
        return date.fromisoformat((value or "")[:10]).isoformat()
    except ValueError:
        return ""


def make_new(session: Session, guess: Mapping) -> dict[str, list[str]]:
    """On approval: a new room or person becomes a subject (S5). Not committed here: the approval commits it."""
    made: dict[str, list[str]] = {"rooms": [], "people": []}
    for item in guess.get("items") or []:
        if item.get("room") and subjects.find(session, SubjectKind.ROOM, item["room"]) is None:
            household.add_room(session, item["room"])
            made["rooms"].append(item["room"])
    who = guess.get("who")
    if who and subjects.find(session, SubjectKind.PERSON, who) is None:
        subjects.register_person(session, who)
        made["people"].append(who)
    return made


def approved(guess: Mapping, *, kind_label: str, received: datetime, by: Optional[str]) -> dict:
    """The guess as approved: new flags settled, the name built for the kind it was filed as."""
    final = {k: v for k, v in guess.items() if k not in ("new_area", "new_kind")}
    for item in final.get("items") or []:
        item["new_room"] = False
    final["who_is_family"] = bool(guess.get("who_is_family"))
    final["filed_name"] = name_for_guess(final, kind_label, received)
    final["approved_by"], final["approved_at"] = by, datetime.utcnow().isoformat(timespec="seconds")
    return final
