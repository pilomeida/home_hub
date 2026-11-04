"""A fake CategorySpec for testing shared code against the registry contract
without depending on any real domain."""

from app.categories.base import (
    CardLine, KindSpec, CategoryCard, CategoryHandler, CategorySpec, EntityTypeSpec,
    FactPolicy, FactSpec, FieldKind, FieldSpec, MediaKind, NavLink, SourceKind, WikiSchema,
)
from app.models.document import DocumentStatus
from app.models.category import Category


class RecordingHandler(CategoryHandler):
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


def make_fake_spec(handler=None, category=Category.HOUSE, infers_kind=False, wiki_guidance=""):
    return CategorySpec(
        category=category,
        label="Fake",
        description="A fake domain used by tests.",
        home_url="/fake",
        kinds=(
            KindSpec("manual", "Manual", "A product manual."),
            KindSpec(
                "clip", "Clip", "A photo or video clip.",
                accepted_media=frozenset({MediaKind.PDF, MediaKind.IMAGE, MediaKind.VIDEO}),
            ),
            KindSpec("visit", "Visit", "A hand-logged visit.", kind=SourceKind.RECORD),
        ),
        fields=(
            FieldSpec("item_name", "Item", FieldKind.SUGGEST, kinds=frozenset({"manual", "visit"}), required=True),
            FieldSpec("side", "Side", FieldKind.CHOICE, kinds=frozenset({"clip"}),
                      choices=(("in", "Indoor"), ("out", "Outdoor"))),
            FieldSpec("seen_on", "Seen on", FieldKind.DATE),
            FieldSpec("observations", "Observations", FieldKind.LONGTEXT, kinds=frozenset({"clip"}),
                      media=frozenset({MediaKind.IMAGE, MediaKind.VIDEO})),
            FieldSpec("visit_date", "Visit date", FieldKind.DATE, kinds=frozenset({"visit"}), required=True),
        ),
        handler=handler or RecordingHandler(),
        nav_links=(NavLink("Fake home", "/fake"),),
        overview_card=lambda session, today: CategoryCard(label="Fake", url="/fake", lines=[CardLine("fake line")]),
        document_url=lambda document: f"/fake/documents/{document.id}",
        wiki=WikiSchema(
            guidance=wiki_guidance,
            entity_types=(
                EntityTypeSpec(
                    page_type="fake.item", label="Items", key_field="item_name",
                    kinds=frozenset({"manual", "visit"}),
                    facts=(
                        FactSpec("type", "Type", "category"),
                        FactSpec("seen", "Seen on", "seen_on", policy=FactPolicy.LATEST, note_field="seen_note"),
                        FactSpec("visited", "Visited", "visit_date", kinds=frozenset({"visit"}), policy=FactPolicy.LATEST),
                    ),
                ),
            ),
        ),
        infers_kind=infers_kind,
        record_url=lambda record: f"/fake/records/{record.id}",
        derive_fields=lambda category, fields: (
            {"seen_note": "assumed"} if fields.get("seen_on") == "2000-01-01" else {}
        ),
    )


SPEC = make_fake_spec()