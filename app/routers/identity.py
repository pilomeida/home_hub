"""Identity & Legal tab: /identity/* (PLAN step 8a), on the shared paper pages."""

from datetime import date

from app.categories.identity.renewals import papers_by_holder
from app.models.category import Category
from app.routers.paper_pages import paper_pages

router = paper_pages(Category.IDENTITY, lambda session: {"groups": papers_by_holder(session, date.today())},
                     "identity/landing.html", upload_note="Leave the details empty and the Hub reads them from the "
                     "paper. Only the private route reads these papers.")
