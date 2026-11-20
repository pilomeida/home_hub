"""Pets tab: /pets/* (PLAN step 8e), on the shared paper pages."""

from datetime import date

from app.categories.pets.spec import pets
from app.models.category import Category
from app.routers.paper_pages import paper_pages

router = paper_pages(Category.PETS, lambda session: {"pets": pets(session, date.today())}, "pets/landing.html",
                     upload_note="Leave the details empty and the Hub reads them from the paper.")
