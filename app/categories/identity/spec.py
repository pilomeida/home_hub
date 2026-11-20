"""Identity & Legal's CategorySpec (PLAN step 8a). Always read at High, like Health: an ID number and a passport
scan are only ever seen by the private route, whether or not the family's levels are binding yet."""

from app.categories.base import CategorySpec, FieldKind, FieldSpec, KindSpec, NavLink, dated_by_fields
from app.categories.identity.handler import IdentityHandler
from app.categories.identity.kinds import EXPIRING, IdentityKind
from app.categories.identity.overview import identity_overview_card
from app.categories.identity.renewals import RENEWAL
from app.models.category import Category
from app.privacy.tiers import Tier

SPEC = CategorySpec(
    category=Category.IDENTITY,
    label="Identity & Legal",
    description=(
        "A household member's identity and legal papers: ID card (Cartão de Cidadão), passport, driving licence, "
        "residence permit or certificate, and wills."
    ),
    home_url="/identity",
    kinds=(
        KindSpec(IdentityKind.ID_CARD.value, "ID card", "A national identity card (Cartão de Cidadão, BI).", reminder=RENEWAL),
        KindSpec(IdentityKind.PASSPORT.value, "Passport", "A passport, any country.", reminder=RENEWAL),
        KindSpec(IdentityKind.DRIVING_LICENCE.value, "Driving licence", "A driving licence (carta de condução).", reminder=RENEWAL),
        KindSpec(IdentityKind.RESIDENCE.value, "Residence papers",
                 "A residence permit or card, or a certificate of residence (título / certificado de residência).", reminder=RENEWAL),
        KindSpec(IdentityKind.WILL.value, "Will", "A will (testamento) or a notary's record of one."),
    ),
    fields=(
        FieldSpec("holder", "Whose it is", FieldKind.SUGGEST, help="Read from the paper when left empty."),
        FieldSpec("document_number", "Document number", FieldKind.TEXT, kinds=EXPIRING,
                  help="Read from the paper when left empty. Shown only here."),
        FieldSpec("issued_on", "Issued on", FieldKind.DATE),
        FieldSpec("expires_on", "Expires on", FieldKind.DATE, kinds=EXPIRING,
                  help="A renewal To-Do comes 90 days before."),
        FieldSpec("country", "Issued by (country)", FieldKind.SUGGEST, kinds=EXPIRING),
        FieldSpec("notes", "Notes", FieldKind.LONGTEXT, help="e.g. where the original is kept."),
    ),
    handler=IdentityHandler(),
    nav_links=(NavLink("Identity & Legal", "/identity"),),
    overview_card=identity_overview_card,
    document_url=lambda document: f"/identity/documents/{document.id}",
    forced_tier=Tier.HIGH,          # never read below High (Claude, 2026-11-20, like Health's D19; Pedro may change)
    dated_by=dated_by_fields("issued_on"),
)
