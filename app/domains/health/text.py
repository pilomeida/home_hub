"""Name folding for matching people, areas and tests ('Évora' == 'evora')."""

import re
import unicodedata


def fold(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text or "")
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch)).lower()


def match_key(text: str) -> str:
    return re.sub(r"\s+", " ", fold(text)).strip()
