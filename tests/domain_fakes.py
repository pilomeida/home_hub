"""A fake DomainSpec for testing shared code against the registry contract
without depending on any real domain."""

from app.domains.base import (
    CardLine, CategorySpec, DomainCard, DomainHandler, DomainSpec, EntityTypeSpec,
    FactPolicy, FactSpec, FieldKind, FieldSpec, MediaKind, NavLink, WikiSchema,
)
from app.models.document import DocumentStatus
from app.models.domain import Domain


class RecordingHandler(DomainHandler):
    def __init__(self, fail_with: Exception | None = None):
        self.processed: list[int] = []
        self.changed: list[tuple[int, dict]] = []
        self.fail_with = fail_with

    async def process(self, session, document):
        self.processed.append(document.id)
        if self.fail_with is not None:
            raise self.fail_with
        document.status = DocumentStatus.PROCESSED
        session.add(document)
        session.commit()
        session.refresh(document)
        return document

    async def on_fields_changed(self, session, document, previous_fields):
        self.changed.append((document.id, previous_fields))


def make_fake_spec(handler=None, domain=Domain.HOUSE, infers_category=False, wiki_guidance=""):
    return DomainSpec(
        domain=domain,
        label="Fake",
        description="A fake domain used by tests.",
        home_url="/fake",
        categories=(
            CategorySpec("manual", "Manual", "A product manual."),
            CategorySpec(
                "clip", "Clip", "A photo or video clip.",
                accepted_media=frozenset({MediaKind.PDF, MediaKind.IMAGE, MediaKind.VIDEO}),
            ),
        ),
        fields=(
            FieldSpec("item_name", "Item", FieldKind.SUGGEST, categories=frozenset({"manual"}), required=True),
            FieldSpec("side", "Side", FieldKind.CHOICE, categories=frozenset({"clip"}),
                      choices=(("in", "Indoor"), ("out", "Outdoor"))),
            FieldSpec("seen_on", "Seen on", FieldKind.DATE),
            FieldSpec("observations", "Observations", FieldKind.LONGTEXT, categories=frozenset({"clip"}),
                      media=frozenset({MediaKind.IMAGE, MediaKind.VIDEO})),
        ),
        handler=handler or RecordingHandler(),
        nav_links=(NavLink("Fake home", "/fake"),),
        overview_card=lambda session, today: DomainCard(label="Fake", url="/fake", lines=[CardLine("fake line")]),
        document_url=lambda document: f"/fake/documents/{document.id}",
        wiki=WikiSchema(
            guidance=wiki_guidance,
            entity_types=(
                EntityTypeSpec(
                    page_type="fake.item", label="Items", key_field="item_name",
                    categories=frozenset({"manual"}),
                    facts=(
                        FactSpec("type", "Type", "category"),
                        FactSpec("seen", "Seen on", "seen_on", policy=FactPolicy.LATEST),
                    ),
                ),
            ),
        ),
        infers_category=infers_category,
    )


SPEC = make_fake_spec()