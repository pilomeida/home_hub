"""Other papers' CategorySpec (PLAN step 1c, named by Pedro 2026-11-19): anything the other categories do not take.

A paper here is found by search (step 1); when step 8 gives contracts, letters and certificates their own kinds,
they move out by re-filing."""

from app.categories.base import CategorySpec, FieldKind, FieldSpec, KindSpec, NavLink, dated_by_fields
from app.categories.other.handler import OtherHandler
from app.categories.other.overview import other_overview_card
from app.models.category import Category
from app.privacy.tiers import Tier

OTHER_PAPER = "other_paper"

SPEC = CategorySpec(
    category=Category.OTHER,
    label="Other papers",
    description=(
        "Any household paper that fits NONE of the other areas: a contract, a letter from a bank or the State, a "
        "certificate or diploma, a notification, a receipt that is not a bill. Choose this only when no other area "
        "fits."
    ),
    home_url="/other",
    kinds=(
        KindSpec(OTHER_PAPER, "Other paper",
                 "A paper that fits none of the other areas: contract, letter, certificate, notification, receipt.",
                 suggested_tier=Tier.MID),
    ),
    fields=(
        FieldSpec("title", "Title", FieldKind.TEXT, help="Read from the paper when left empty."),
        FieldSpec("paper_date", "Date of the paper", FieldKind.DATE),
        FieldSpec("about", "Who or what it is about", FieldKind.SUGGEST, help="A person, the house, a car, an account."),
        FieldSpec("summary", "Summary", FieldKind.LONGTEXT, help="Read from the paper when left empty."),
    ),
    handler=OtherHandler(),
    nav_links=(NavLink("Other papers", "/other"),),
    overview_card=other_overview_card,
    document_url=lambda document: f"/other/documents/{document.id}",
    catch_all=True,
    dated_by=dated_by_fields("paper_date"),
)
