"""Education's CategorySpec (PLAN step 8d): school papers per child, school fees, diplomas. A school paper's date (a
deadline, an enrolment, a meeting) and a fee's due date become To-Dos, so they show in Coming up and reach the phone
once. The child is a person of the subjects registry."""

from datetime import date

from sqlmodel import Session

from app.categories.base import CardLine, CategoryCard, CategorySpec, FieldKind, FieldSpec, KindSpec, NavLink, dated_by_fields
from app.categories.entries import category_entries
from app.categories.fields import load_fields
from app.categories.reminders import DateReminder, first_name
from app.categories.typed_handler import TypedPaperHandler
from app.models.category import Category
from app.models.subject import SubjectKind
from app.privacy.tiers import Tier

SCHOOL_PAPER, FEE, DIPLOMA = "school_paper", "school_fee", "diploma"
KIND_LABELS = {SCHOOL_PAPER: "School paper", FEE: "School fee", DIPLOMA: "Diploma or certificate"}
DATE_BEFORE, FEE_BEFORE = 7, 7          # days (Claude, 2026-11-20; Pedro may change)


def _who(fields: dict) -> str:
    return first_name(fields.get("student") or "") if fields.get("student") else "the family"


def _fee_title(doc, f, when: date) -> str:
    amount = f" €{f['amount']}" if f.get("amount") else ""
    return f"Pay the school fee for {_who(f)}{amount} (due {when.isoformat()})"


SCHOOL_DATE = DateReminder(
    field="date_to_note", days_before=DATE_BEFORE,
    title=lambda doc, f, when: f"{f.get('what') or 'School date'} for {_who(f)} ({when.isoformat()})",
    phone=lambda doc, f, when: f"A school date for {_who(f)} is on {when.isoformat()}: see To-Dos.")
FEE_DUE = DateReminder(
    field="due_on", days_before=FEE_BEFORE, title=_fee_title,
    phone=lambda doc, f, when: f"A school fee for {_who(f)} is due on {when.isoformat()}: see To-Dos.")

_SYSTEM = """You read one education paper of a household: a school paper about a child (enrolment, report, letter,
timetable), a school fee invoice or notice, or a diploma or certificate. Return ONLY a JSON object:
{"student": "...", "school": "...", "school_year": "...", "paper_date": "YYYY-MM-DD or null",
 "what": "...", "date_to_note": "YYYY-MM-DD or null", "amount": "...", "due_on": "YYYY-MM-DD or null"}
- student: the full name of the pupil or the person the diploma is for; school: the school or institution.
- school_year: e.g. "2026/2027"; "" if none. paper_date: the date the paper was issued.
- what and date_to_note: on a school paper, the one date a parent must act on or attend (a deadline, an enrolment,
  a meeting) and in a few words what it is ("Enrolment deadline"); null and "" if none.
- amount and due_on: on a fee, the amount in euros (digits and a decimal point only) and the payment deadline.
Names and dates as printed (dd/mm/aaaa in Portuguese papers); null or "" for anything not printed."""


def education_overview_card(session: Session, today: date) -> CategoryCard:
    entries = [e for e in category_entries(session, Category.EDUCATION) if e.document is not None]
    students = {load_fields(e.document).get("student") for e in entries} - {None, ""}
    lines = [CardLine(text=f"{len(entries)} paper{'s' if len(entries) != 1 else ''} on file", url="/education")]
    if students:
        lines.append(CardLine(text=f"{len(students)} pupil{'s' if len(students) != 1 else ''}", url="/education"))
    return CategoryCard(label="Education", url="/education", lines=lines)


SPEC = CategorySpec(
    category=Category.EDUCATION,
    label="Education",
    description="The children's school papers (enrolment, reports, school letters), school fee invoices or notices, "
                "and anyone's diplomas and certificates of study.",
    home_url="/education",
    from_guess={"student": "who", "paper_date": "paper_date"},
    kinds=(
        KindSpec(SCHOOL_PAPER, "School paper", "A school's paper about a child: enrolment, report, letter, timetable.",
                 suggested_tier=Tier.MID, reminder=SCHOOL_DATE),
        KindSpec(FEE, "School fee", "A school or activity fee: invoice, notice or payment plan for a child.",
                 suggested_tier=Tier.MID, reminder=FEE_DUE),
        KindSpec(DIPLOMA, "Diploma or certificate", "A diploma or certificate of studies, anyone's.",
                 suggested_tier=Tier.MID),
    ),
    fields=(
        FieldSpec("student", "Who", FieldKind.SUGGEST, help="The pupil, or whose diploma it is."),
        FieldSpec("school", "School", FieldKind.SUGGEST),
        FieldSpec("school_year", "School year", FieldKind.TEXT, kinds=frozenset({SCHOOL_PAPER, FEE}),
                  help="e.g. 2026/2027."),
        FieldSpec("paper_date", "Date of the paper", FieldKind.DATE),
        FieldSpec("what", "What the date is", FieldKind.TEXT, kinds=frozenset({SCHOOL_PAPER}),
                  help="e.g. Enrolment deadline, parents' meeting."),
        FieldSpec("date_to_note", "Date to act on", FieldKind.DATE, kinds=frozenset({SCHOOL_PAPER}),
                  help="A To-Do comes 7 days before, and it shows in Coming up."),
        FieldSpec("amount", "Amount (€)", FieldKind.TEXT, kinds=frozenset({FEE})),
        FieldSpec("due_on", "Due on", FieldKind.DATE, kinds=frozenset({FEE}),
                  help="A To-Do comes 7 days before, and it shows in Coming up."),
        FieldSpec("notes", "Notes", FieldKind.LONGTEXT),
    ),
    handler=TypedPaperHandler(_SYSTEM, ("student", "school", "school_year", "paper_date", "what", "date_to_note",
                                        "amount", "due_on"), ("paper_date", "date_to_note", "due_on"), "student",
                              SubjectKind.PERSON, {"date_to_note": "the date to act on", "due_on": "the due date"},
                              expects_date=frozenset({FEE})),
    nav_links=(NavLink("Education", "/education"),),
    overview_card=education_overview_card,
    document_url=lambda document: f"/education/documents/{document.id}",
    dated_by=dated_by_fields("paper_date"),
)


def papers_by_student(session: Session) -> list[tuple[str, list[tuple]]]:
    groups: dict[str, list[tuple]] = {}
    for e in category_entries(session, Category.EDUCATION):
        if e.document is None:
            continue
        f = load_fields(e.document)
        when = f.get("date_to_note") or f.get("due_on") or ""
        note = " · ".join(filter(None, [f.get("school", ""), f.get("school_year", ""), f.get("what", ""), when,
                                        f"€{f['amount']}" if f.get("amount") else ""]))
        groups.setdefault(f.get("student") or "Who it is for not known yet", []).append(
            (e.document, KIND_LABELS.get(e.document.paper_kind or "", "Paper"), f.get("paper_date", ""), note))
    for papers in groups.values():
        papers.sort(key=lambda p: p[2] or "", reverse=True)
    return sorted(groups.items(), key=lambda kv: kv[0].lower())
