"""Other papers' CategorySpec (PLAN step 1c, named by Pedro 2026-11-19): anything the other categories do not take.

A paper here is found by search (step 1). Step 8b gave contracts and letters kinds of their own, each with its own
level on the Privacy page; the papers filed before moved by re-filing (`app/categories/other/refile.py`)."""

from app.categories.base import CategorySpec, FieldKind, FieldSpec, KindSpec, NavLink, dated_by_fields
from app.categories.other.handler import OtherHandler
from app.categories.other.overview import other_overview_card
from app.models.category import Category
from app.privacy.tiers import Tier

OTHER_PAPER = "other_paper"
CONTRACT = "contract"
LETTER = "letter"
KIND_ORDER = (CONTRACT, LETTER, OTHER_PAPER)

SPEC = CategorySpec(
    category=Category.OTHER,
    label="Other papers",
    description=(
        "Any household paper that fits NONE of the other areas: a contract, a letter from a bank or the State, a "
        "certificate or diploma, a notification, a receipt that is not a bill. Choose this only when no other area "
        "fits."
    ),
    home_url="/other",
    kinds=(                     # the generic kind first: the Inbox offers it when nothing else fits
        KindSpec(OTHER_PAPER, "Other paper",
                 "A paper that fits none of the other areas and is neither a contract nor a letter: a certificate, "
                 "a receipt that is not a bill, anything else.", suggested_tier=Tier.MID),
        KindSpec(CONTRACT, "Contract",
                 "A contract or agreement the household signed: telecom or energy contract, service agreement, NDA, "
                 "rental or purchase agreement, its terms and conditions or a change to it.", suggested_tier=Tier.MID),
        KindSpec(LETTER, "Letter",
                 "A letter or notice to the household from a bank, a company or the State: a bank's FATCA/CRS "
                 "self-certification request, a notification, a change of terms.", suggested_tier=Tier.MID),
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
