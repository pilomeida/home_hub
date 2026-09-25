"""Domain: which area of the household a record belongs to. Every member
must have a matching DomainSpec registered in app/domains/registry.py
before it is used, and adding a member needs a migration that alters the
`domain` column on documents, todos and wiki_pages (values are stored by
NAME). See docs/ARCHITECTURE.md § "Adding a domain"."""

from enum import Enum


class Domain(str, Enum):
    FINANCIALS = "financials"
    HOUSE = "house"