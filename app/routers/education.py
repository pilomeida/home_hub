"""Education tab: /education/* (PLAN step 8d), on the shared paper pages."""

from app.categories.education.spec import papers_by_student
from app.models.category import Category
from app.ui.paper_pages import paper_pages

router = paper_pages(Category.EDUCATION, "/education", lambda session: {"groups": papers_by_student(session)},
                     "education/landing.html", by_who=True, upload_note="Leave the details empty and the Hub reads them from the "
                     "paper. A date to act on and a fee's due date become To-Dos and show in Coming up.")
