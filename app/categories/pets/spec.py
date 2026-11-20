"""Pets' CategorySpec (PLAN step 8e): a pet is a thing of the subjects registry with a type, like the car: its vet
visits and vaccinations, each with the next date as a To-Do that reaches the phone once. A pet's insurance is filed
under Insurance (what it covers: pet) and shows on its card."""

from datetime import date

from sqlmodel import Session

from app.categories.base import CardLine, CategoryCard, CategorySpec, FieldKind, FieldSpec, KindSpec, NavLink, dated_by_fields
from app.categories.entries import category_entries
from app.categories.fields import field_date, load_fields
from app.categories.reminders import DateReminder
from app.categories.typed_handler import TypedPaperHandler
from app.models.category import Category
from app.models.subject import SubjectKind
from app.privacy.tiers import Tier

VET, VACCINATION = "vet_visit", "vaccination"
KIND_LABELS = {VET: "Vet visit", VACCINATION: "Vaccination"}
SPECIES = (("dog", "Dog"), ("cat", "Cat"), ("other", "Other"))
VISIT_BEFORE, VACCINE_BEFORE = 14, 14   # days (Claude, 2026-11-20; Pedro may change)


def _pet(f: dict) -> str:
    return (f.get("pet") or "").strip() or "the pet"


NEXT_VISIT = DateReminder(
    field="next_on", days_before=VISIT_BEFORE,
    title=lambda doc, f, when: f"Book the vet for {_pet(f)} (due {when.isoformat()})",
    phone=lambda doc, f, when: f"A vet visit for {_pet(f)} is due on {when.isoformat()}: see To-Dos.")
NEXT_VACCINE = DateReminder(
    field="next_on", days_before=VACCINE_BEFORE,
    title=lambda doc, f, when: f"{f.get('vaccine') or 'Vaccination'} for {_pet(f)} (due {when.isoformat()})",
    phone=lambda doc, f, when: f"A vaccination for {_pet(f)} is due on {when.isoformat()}: see To-Dos.")

_SYSTEM = """You read one paper about a household's pet: a vet's invoice or visit record, or a vaccination record
or booklet page. Return ONLY a JSON object:
{"pet": "...", "species": "dog|cat|other|", "paper_date": "YYYY-MM-DD or null", "vaccine": "...",
 "next_on": "YYYY-MM-DD or null"}
- pet: the animal's name as printed; species: dog, cat or other ("" if not clear).
- paper_date: the date of the visit or of the vaccination.
- vaccine: on a vaccination, the vaccine's name (e.g. "Rabies"); "" otherwise.
- next_on: the next visit or the next vaccination date if printed ("proxima vacina", "revacinacao"); null if none.
Dates as printed (dd/mm/aaaa in Portuguese papers); null or "" for anything not printed. Never guess a date."""


def pets(session: Session, today: date) -> list[dict]:
    from app.categories.insurance.policies import build_policy_cards
    cards: dict[str, dict] = {}
    for e in category_entries(session, Category.PETS):
        if e.document is None:
            continue
        f = load_fields(e.document)
        name = f.get("pet") or "Name not known yet"
        card = cards.setdefault(name, {"name": name, "species": "", "papers": [], "next": None, "policies": []})
        card["species"] = card["species"] or dict(SPECIES).get(f.get("species", ""), "")
        card["papers"].append((e.document, KIND_LABELS.get(e.document.paper_kind or "", "Paper"),
                               f.get("paper_date", ""), f.get("vaccine", "")))
        when = field_date(f, "next_on")
        if when is not None and (card["next"] is None or when < card["next"][0]) and when >= today:
            card["next"] = (when, KIND_LABELS.get(e.document.paper_kind or "", "Visit"))
    policies = [p for p in build_policy_cards(session) if p.covers == "pet"]
    for card in cards.values():
        card["papers"].sort(key=lambda p: p[2] or "", reverse=True)
        card["policies"] = [p for p in policies if card["name"].lower() in (p.insured or "").lower()]
    return sorted(cards.values(), key=lambda c: c["name"].lower())


def pets_overview_card(session: Session, today: date) -> CategoryCard:
    all_pets = pets(session, today)
    lines = [CardLine(text=f"{len(all_pets)} pet{'s' if len(all_pets) != 1 else ''}", url="/pets")]
    upcoming = sorted((c["next"][0], c["name"], c["next"][1]) for c in all_pets if c["next"])
    if upcoming:
        when, name, what = upcoming[0]
        lines.append(CardLine(text=f"next: {what.lower()} for {name} on {when.isoformat()}", url="/pets"))
    return CategoryCard(label="Pets", url="/pets", lines=lines)


SPEC = CategorySpec(
    category=Category.PETS,
    label="Pets",
    description="The household's pets: vet invoices and visit records, vaccination records. Pet insurance is under "
                "Insurance.",
    home_url="/pets",
    kinds=(
        KindSpec(VET, "Vet visit", "A vet's invoice or record of a visit, treatment or surgery.",
                 suggested_tier=Tier.MID, reminder=NEXT_VISIT),
        KindSpec(VACCINATION, "Vaccination", "A pet's vaccination record or booklet page.",
                 suggested_tier=Tier.MID, reminder=NEXT_VACCINE),
    ),
    fields=(
        FieldSpec("pet", "Pet", FieldKind.SUGGEST, help="Its name. Read from the paper when left empty."),
        FieldSpec("species", "Type", FieldKind.CHOICE, choices=SPECIES),
        FieldSpec("paper_date", "Date", FieldKind.DATE),
        FieldSpec("vaccine", "Vaccine", FieldKind.TEXT, kinds=frozenset({VACCINATION})),
        FieldSpec("next_on", "Next due", FieldKind.DATE, help="A To-Do comes 14 days before."),
        FieldSpec("notes", "Notes", FieldKind.LONGTEXT),
    ),
    handler=TypedPaperHandler(_SYSTEM, ("pet", "species", "paper_date", "vaccine", "next_on"),
                              ("paper_date", "next_on"), "pet", SubjectKind.THING, {"next_on": "the next date"},
                              expects_date=frozenset({VACCINATION}), choices={"species": {v for v, _ in SPECIES}}),
    nav_links=(NavLink("Pets", "/pets"),),
    overview_card=pets_overview_card,
    document_url=lambda document: f"/pets/documents/{document.id}",
    dated_by=dated_by_fields("paper_date"),
)
