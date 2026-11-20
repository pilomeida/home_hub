"""Domain: which area of the household a record belongs to. In the model's words this is a CATEGORY (Money &
Bills, Health, House Records, Insurance); see the glossary at the top of docs/ARCHITECTURE.md. Every member
must have a matching CategorySpec registered in app/categories/registry.py
before it is used, and adding a member needs a migration that alters the
`domain` column on documents, todos and wiki_pages (values are stored by
NAME). See docs/ARCHITECTURE.md § "Adding a domain"."""

from enum import Enum


class Category(str, Enum):
    FINANCIALS = "financials"
    HOUSE = "house"
    HEALTH = "health"
    INSURANCE = "insurance"  # stored as VARCHAR(10) without a CHECK: a new member needs no migration
    OTHER = "other"          # "Other papers", the catch-all (PLAN step 1c)
    IDENTITY = "identity"    # Identity & Legal (PLAN step 8a): ID cards, passports, licences, residence papers, wills
    VEHICLES = "vehicles"    # Vehicles (PLAN step 8c): registration, inspection, road tax, service
    EDUCATION = "education"  # Education (PLAN step 8d): school papers per child, fees, diplomas
    PETS = "pets"            # Pets (PLAN step 8e): vet visits, vaccinations