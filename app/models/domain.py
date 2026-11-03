"""Domain: which area of the household a record belongs to. In the model's words this is a CATEGORY (Money &
Bills, Health, House Records, Insurance); see the glossary at the top of docs/ARCHITECTURE.md. Every member
must have a matching DomainSpec registered in app/domains/registry.py
before it is used, and adding a member needs a migration that alters the
`domain` column on documents, todos and wiki_pages (values are stored by
NAME). See docs/ARCHITECTURE.md § "Adding a domain"."""

from enum import Enum


class Domain(str, Enum):
    FINANCIALS = "financials"
    HOUSE = "house"
    HEALTH = "health"
    INSURANCE = "insurance"  # stored as VARCHAR(10) without a CHECK: a new member needs no migration