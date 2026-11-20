"""Vehicles tab: /vehicles/* (PLAN step 8c), on the shared paper pages."""

from datetime import date

from app.categories.vehicles.garage import cars
from app.models.category import Category
from app.ui.paper_pages import paper_pages

router = paper_pages(Category.VEHICLES, "/vehicles", lambda session: {"cars": cars(session, date.today())}, "vehicles/landing.html",
                     upload_note="Leave the details empty and the Hub reads them from the paper.")
