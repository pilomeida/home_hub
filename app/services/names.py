"""The one name normaliser for matching names across the Hub (work-list B4): 'Évora' == 'evora', 'RUTE  Correia'
== 'rute correia'. People, Health's areas and tests, and the subjects registry use it. (A payee's key from bank
text, services/classification_engine.normalize_provider, strips store codes too: a different job. The wiki's own
key moves here when wiki entity keys point at subjects.)"""

import re
import unicodedata

# Words that join a Portuguese name and say nothing about who it is ("Rute da Silva").
NAME_PARTICLES = frozenset({"da", "de", "do", "das", "dos", "e"})


def fold(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text or "")
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch)).lower()


def name_key(text: str) -> str:
    return re.sub(r"\s+", " ", fold(text)).strip()


def name_tokens(text: str) -> list[str]:
    """The words that identify: no particles, no punctuation."""
    return [w for w in re.split(r"[^a-z0-9]+", name_key(text)) if w and w not in NAME_PARTICLES]
