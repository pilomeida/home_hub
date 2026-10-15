"""Plain-language names for the Hub's AI jobs and for the gateway's existing
levels, for the settings screen. The levels themselves live in the gateway;
these are only labels."""

JOBS = {
    "vision_extraction": ("Reading bills, statements and warranties", "Pulls the amounts, dates and details out of a document."),
    "vision_classification": ("Deciding what a document is", "Works out whether a file is a bill, a statement, an insurance paper, and so on."),
    "classification": ("Sorting merchants and wiki facts", "Labels shops and decides what is worth remembering."),
    "critique_review": ("Weekly wiki health check", "Looks for out-of-date or conflicting facts."),
    "interactive_research": ("Ask", "Answers your questions from the wiki and documents."),
}

PRIVACY_LABELS = {
    "public": "Public — anything goes",
    "business": "Business",
    "personal": "Personal — about the family",
    "private": "Private — the AI provider must promise not to keep copies",
}

QUALITY_LABELS = {
    "low": "Basic models are fine",
    "high": "Strong models only",
    "top": "Best models only",
}


def job_label(workload_type: str) -> tuple[str, str]:
    return JOBS.get(workload_type, (workload_type.replace("_", " ").capitalize(), ""))
