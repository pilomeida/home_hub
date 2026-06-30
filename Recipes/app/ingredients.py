"""Shared ingredient normalization helpers and harmonization I/O."""

import json
import re
from pathlib import Path

_HARM_FILE = Path("data/ingredient_harmonization.json")

# ── Normalization regexes ─────────────────────────────────────────────────────

_PLUS_RE     = re.compile(r'^\+\s*')
_OPTIONAL_RE = re.compile(r'^\(optional\)\s*', re.IGNORECASE)
_PAREN_RE    = re.compile(r'\s*\([^)]*\)')
_QTY_RE      = re.compile(
    r'^[½¼¾⅓⅔⅛⅜⅝⅞\d./\s]+'
    r'(?:heaping\s+|level\s+)?'
    r'(?:g|ml|mL|dl|dL|l|L|kg|lb|lbs|oz|tbsp|tsp|tablespoons?|teaspoons?|cups?'
    r'|dessert\s+spoons?|pieces?|slices?|cloves?|pinch(?:es)?|handful|drops?'
    r'|sprigs?|scoops?|cans?|squares?|cubes?|fillets?|sheets?|stalks?|heads?'
    r'|bunches?|bags?|jars?|bottles?|packets?|sachets?|servings?)?'
    r'\s*',
    re.IGNORECASE,
)
# Colloquial quantity words not preceded by a number
_COLLOQUIAL_QTY_RE = re.compile(
    r'^(?:hips?|heaps?|bits?|loads?|bunch(?:es)?|splash(?:es)?|squeeze|knob|'
    r'dollop|dash|drizzle|sprinkle|dusting|smidge|trace|touch|handful)\s+(?:of\s+)?',
    re.IGNORECASE,
)
_INDEF_RE    = re.compile(r'^(?:a\s+few\s+\w+|a\s+handful|some|an?)\s+(?:of\s+)?', re.IGNORECASE)
_OF_RE       = re.compile(r'^of\s+', re.IGNORECASE)
_ARTICLE_RE  = re.compile(r'^(?:a|an)\s+', re.IGNORECASE)

# Applied repeatedly until stable — handles stacked adjectives like "large frozen ripe"
_PREP_RE = re.compile(
    r'^(?:or\s+)?'
    r'(?:'
    # preparation methods
    r'cooked(?:\s+and\s+drained)?|'
    r'chopped|finely\s+chopped|roughly\s+chopped|coarsely\s+chopped|'
    r'diced|sliced|thinly\s+sliced|thickly\s+sliced|'
    r'minced|crushed|grated|finely\s+grated|'
    r'shredded|ground|roasted|toasted|dried|frozen|canned|ripe|powdered|'
    r'boiled|fried|deep-fried|baked|steamed|saut[eé]ed|grilled|blanched|'
    r'braised|poached|smoked|cured|dehydrated|pickled|marinated|caramelized|'
    r'blended|pur[eé]ed|mashed|crumbled|strained|sifted|whisked|beaten|'
    # treatment
    r'peeled|pitted|seeded|deseeded|destemmed|halved|quartered|'
    r'skinless|boneless|lean|skinned|trimmed|deboned|'
    r'washed|drained|rinsed|squeezed|zested|'
    # size / shape descriptors
    r'large|medium|small|big|fat|tiny|giant|mini|thick|thin|bite-?sized?|'
    # state / condition
    r'fresh|raw|overripe|spotted|ripe|soft|firm|wilted|runny|solid|'
    # pre-prepared
    r'pre-baked|pre-steamed|pre-cooked|pre-soaked|pre-washed|pre-cut|'
    # diet / quality labels
    r'low-fat|fat-free|full-fat|sugar-free|dairy-free|gluten-free|'
    r'organic|unsweetened|sweetened|unsweet|no-?added-?sugar|'
    r'low-sodium|reduced-fat|skimmed|semi-skimmed|'
    # filler quality words
    r'nice|good|great|beautiful|perfect|'
    # age / maturity
    r'baby|young|aged|mature|old|'
    # misc leading qualifiers
    r'extra|plain|natural|pure|'
    r')\s+',
    re.IGNORECASE,
)

_OR_ALT_RE    = re.compile(r'\s*,?\s+or\s+.+$', re.IGNORECASE)
_TO_TASTE_RE  = re.compile(r'\s+to\s+taste\s*$', re.IGNORECASE)
_SECTION_RE   = re.compile(r'^(?:for\s+the|to\s+serve|to\s+garnish|for\s+garnish'
                            r'|for\s+topping|note[:\s]|tip[:\s])', re.IGNORECASE)

_COMPOUND_SPLIT_RE = re.compile(r',\s*(?:and\s+)?|\s+and\s+', re.IGNORECASE)
_SIMPLE_AND_RE     = re.compile(r'^([\w][\w-]*)\s+and\s+([\w][\w-]*)$', re.IGNORECASE)


def _singularize(s: str) -> str:
    """Best-effort English singularization applied to the last word."""
    words = s.split()
    if not words:
        return s
    last = words[-1]
    lo = last.lower()
    if lo.endswith('ies') and len(last) > 4:
        last = last[:-3] + 'y'                   # cherries → cherry
    elif lo.endswith('ves') and len(last) > 4:
        last = last[:-3] + 'f'                   # leaves → leaf
    elif lo.endswith('oes') and len(last) > 5:
        last = last[:-2]                          # tomatoes → tomato
    elif (lo.endswith('s')
          and not lo.endswith(('ss', 'us', 'is', 'ous', 'news', 'ics'))
          and len(last) > 3):
        last = last[:-1]                          # eggs → egg, carrots → carrot
    words[-1] = last
    return ' '.join(words)


def norm_ingredient(raw: str) -> str:
    s = raw.strip()
    # Drop section headers / instructional lines entirely
    if _SECTION_RE.match(s):
        return ''
    s = _PLUS_RE.sub('', s)
    s = _OPTIONAL_RE.sub('', s)
    s = _PAREN_RE.sub('', s).strip()
    s = _INDEF_RE.sub('', s)
    s = _COLLOQUIAL_QTY_RE.sub('', s)
    s = _QTY_RE.sub('', s)
    s = _OF_RE.sub('', s)
    s = _ARTICLE_RE.sub('', s)
    # Strip stacked prep/size adjectives (loop until stable)
    for _ in range(6):
        stripped = _PREP_RE.sub('', s)
        if stripped == s:
            break
        s = stripped
    s = _OR_ALT_RE.sub('', s)
    s = _TO_TASTE_RE.sub('', s)
    s = s.strip(' ,.-–')
    s = _singularize(s)
    return s.capitalize() if s else ''


def expand_ingredient(raw: str) -> list[str]:
    """Normalize and split compound ingredients ('salt and pepper' → ['Salt', 'Pepper'])."""
    normed = norm_ingredient(raw)
    if not normed:
        return []
    if ',' in normed or _SIMPLE_AND_RE.match(normed):
        parts = [p.strip().capitalize() for p in _COMPOUND_SPLIT_RE.split(normed) if p.strip()]
        if len(parts) > 1:
            return parts
    return [normed]


# ── Harmonization I/O ─────────────────────────────────────────────────────────

def load_harmonization() -> dict[str, str]:
    try:
        return json.loads(_HARM_FILE.read_text()) if _HARM_FILE.exists() else {}
    except Exception:
        return {}


def save_harmonization(mapping: dict[str, str]) -> None:
    _HARM_FILE.parent.mkdir(parents=True, exist_ok=True)
    # Remove entries that map to themselves (no-ops)
    clean = {k: v for k, v in mapping.items() if k != v}
    _HARM_FILE.write_text(json.dumps(clean, indent=2, ensure_ascii=False, sort_keys=True))


# ── Suggestion engine ─────────────────────────────────────────────────────────

_TYPO_MAP = {
    "arge ": "Large ",
    "arlic": "Garlic",
    "eek":   "Leek",
    "ight ": "Light ",
    "rilled": "Grilled",
    "rated ": "Grated ",
    "d red onion": "Red onion",
}

_DROP_PATTERNS = [
    "filling ideas", "recipe in this ebook", "yogurt sauce", "fit mayo",
    "protein guacamole", "chocolate frosting", "seeds of choice",
    "other vegetables", "for topping", "for sprinkling",
    "to get the blender", "thin round slice", "as needed",
    "for garnish", "for decoration",
]

# Normalized names that are ONLY a prep verb / non-ingredient fragment → suggest drop
_STANDALONE_NON_INGREDIENTS = {
    "chopped", "grated", "minced", "finely chopped", "thinly sliced",
    "shredded", "diced", "crushed", "sliced", "powdered",
    "no added sugar", "sugar-free", "to taste", "as needed", "optional",
}

# (match_substrings, canonical_or_callable)
_CANONICAL_RULES: list[tuple[list[str], object]] = [
    (["juice of"], "Lemon juice"),
    (["avocado", "guacamole"], "Avocado"),
    (["85% dark chocolate", "85% sugar-free", "dark sugar-free chocolate",
      "cube of 85%", "cubes of 85%", "square 85%"],
     lambda n: "Sugar-free dark chocolate chip" if "chip" in n else "Sugar-free dark chocolate"),
    (["greek yogurt", "greek yoghurt"], "Greek yogurt"),
    (["whole wheat wrap", "flour wrap", "mini wrap", "mini whole wheat"], "Whole wheat wrap"),
    (["olive oil"], "Olive oil"),
    (["garlic clove", "garlic paste", "arlic clove", "arlic cloves"], "Garlic clove"),
    (["egg white"], "Egg white"),
    (["unsweetened almond milk"], "Almond milk"),
    (["shrimp meat"], "Shrimp"),
    (["tuna in natural juice", "tuna or salmon"], "Tuna"),
    (["salmon loin"], "Salmon"),
    (["turkey or chicken ham", "turkey/chicken ham"], "Turkey ham"),
    (["chicken breast cut into strip"], "Chicken breast"),
    (["blended chicken breast", "shredded/minced chicken",
      "or ground chicken", "or minced chicken", "or shredded chicken"], "Chicken"),
    (["lemon juice", "lemon peel"], "Lemon"),
    (["pitted date", "rilled date", "grilled date"], "Date"),
    (["low-fat yogurt +"], "Greek yogurt"),
]


def suggestions_for(normalized: str) -> list[str]:
    n = normalized.lower()
    results: list[str] = []

    # Typo fixes
    for typo, fix in _TYPO_MAP.items():
        if n.startswith(typo.lower()) or n == typo.strip().lower():
            corrected = (fix + normalized[len(typo):]).strip()
            results.append(corrected)
            break

    # Drop signals
    if any(pat in n for pat in _DROP_PATTERNS) or n in _STANDALONE_NON_INGREDIENTS:
        results.append("")

    # Canonical rules
    for keywords, canonical in _CANONICAL_RULES:
        if any(kw.lower() in n for kw in keywords):
            val = canonical(n) if callable(canonical) else canonical
            if val not in results:
                results.append(val)

    # Deduplicate and remove self-matches
    seen: set[str] = set()
    out: list[str] = []
    for s in results:
        if s != normalized and s not in seen:
            seen.add(s)
            out.append(s)
    return out
