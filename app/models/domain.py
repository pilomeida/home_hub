"""Domain: which area of the household this record belongs to. FINANCIALS
is the only implemented domain today -- new members get added here as each
future domain tab (House, Health, Education, Vehicles, Legal & Identity)
ships. See brainstorms/2026-09-14-home-hub-tabs-restructure.md."""

from enum import Enum


class Domain(str, Enum):
    FINANCIALS = "financials"
