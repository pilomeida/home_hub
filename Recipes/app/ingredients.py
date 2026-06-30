"""Shared ingredient normalization helpers and harmonization I/O."""

import json
import re
from pathlib import Path

_HARM_FILE = Path("data/ingredient_harmonization.json")

# ── Normalization regexes ─────────────────────────────────────────────────────

_PLUS_RE        = re.compile(r'^[+&]\s*')   # strip leading + or &
_OPTIONAL_RE    = re.compile(r'^\(optional\)\s*|^optional\s*[-–:]\s*', re.IGNORECASE)
_PAREN_RE       = re.compile(r'\s*\([^)]*\)')
_JUICE_ZEST_RE  = re.compile(                 # "juice of a lemon" → "lemon"
    r'^(?:the\s+)?(?:juice|zest)\s+of\s+'
    r'(?:[½¼\d/]+\s+)?(?:a\s+|an\s+|one\s+|half\s+(?:a\s+)?)?',
    re.IGNORECASE,
)
_QTY_RE = re.compile(
    r'^[½¼¾⅓⅔⅛⅜⅝⅞\d./\s%+]+'              # +: covers "1 + 1/2 cups"
    r'(?:heaping\s+|level\s+)?'
    r'(?:(?:g|ml|mL|dl|dL|l|L|kg|lb|lbs|oz|tbsp|tsp|tablespoons?|teaspoons?|cups?'
    r'|dessert\s+spoons?|pieces?|slices?|cloves?|pinch(?:es)?|handful|drops?'
    r'|sprigs?|scoops?|cans?|squares?|cubes?|fillets?|sheets?|stalks?|heads?'
    r'|bunches?|bags?|jars?|bottles?|packets?|sachets?|servings?'
    r'|tins?|packs?|balls?|blocks?|thumbs?|sticks?|layers?|cloves?)\b)?'
    r'\s*',
    re.IGNORECASE,
)
# Colloquial container/quantity words not preceded by a number
_COLLOQUIAL_QTY_RE = re.compile(
    r'^(?:hips?|heaps?|bits?|loads?|splash(?:es)?|squeeze|knob|'
    r'dollop|dash|drizzle|sprinkle|dusting|smidge|trace|touch|handful|'
    r'box(?:es)?|container(?:s)?|package(?:s)?|pouch(?:es)?|carton(?:s)?|'
    r'tin(?:s)?|ball(?:s)?|bulb(?:s)?|block(?:s)?|pack(?:s)?|thumb(?:s)?|'
    r'head(?:s)?|layer(?:s)?|strip(?:s)?|spray|drop(?:s)?|'
    r'piece(?:s)?|cube(?:s)?|stick(?:s)?|sheet(?:s)?|pinch(?:es)?|'
    r'slice(?:s)?|scoop(?:s)?|bunch(?:es)?|jar(?:s)?|can(?:s)?|bottle(?:s)?|'
    r'bag(?:s)?|clove(?:s)?)\s+(?:of\s+)?',
    re.IGNORECASE,
)
_OF_CHOICE_RE   = re.compile(r'\s+of\s+choice\s*$', re.IGNORECASE)
_INDEF_RE       = re.compile(
    r'^(?:a\s+few\s+\w+|a\s+handful|some|an?|your\s+favou?rite|my)\s+(?:of\s+)?',
    re.IGNORECASE,
)
_OF_RE          = re.compile(r'^of\s+', re.IGNORECASE)
_ARTICLE_RE     = re.compile(r'^(?:a|an|the)\s+', re.IGNORECASE)

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
    r'brewed|soaked|tinned|reduced|whipped|spreadable|'
    # treatment
    r'peeled|pitted|seeded|deseeded|destemmed|halved|quartered|'
    r'skinless|boneless|lean|skinned|trimmed|deboned|'
    r'washed|drained|rinsed|squeezed|zested|'
    # size / shape
    r'large|medium|small|big|fat|tiny|giant|mini|thick|thin|bite-?sized?|huge|'
    # state / condition
    r'fresh|raw|overripe|spotted|spotty|ripe|soft|firm|wilted|runny|solid|'
    # pre-prepared (with or without hyphen/space)
    r'pre-?\s*(?:baked|steamed|cooked|soaked|washed|cut)|'
    # diet / quality labels (hyphen-optional variants)
    r'low-?\s*fat|fat-?free|full-?fat|sugar-?free|dairy-?free|gluten-?free|'
    r'organic|unsweetened|sweetened|unsweet|no-?added-?sugar|'
    r'low-?sodium|reduced-?fat|skimmed|semi-?skimmed|oil-?free|'
    r'plant-?\s*based|vegan|light|'
    # filler quality / source words
    r'nice|good|great|beautiful|perfect|additional|homemade|'
    # age / maturity
    r'baby|young|aged|mature|old|'
    # misc leading qualifiers
    r'extra|plain|natural|pure|'
    r')(?:\s+|$)',
    re.IGNORECASE,
)

_OR_ALT_RE            = re.compile(r'\s*,?\s+or\s+.+$', re.IGNORECASE)
_TRAILING_COMMA_PREP_RE = re.compile(  # ", chopped", ", drained & rinsed", etc.
    r'\s*,\s*(?:[+&]\s*)?'
    r'(?:halved|sliced|thinly\s+sliced|finely\s+sliced|'
    r'chopped|finely\s+chopped|roughly\s+chopped|coarsely\s+chopped|'
    r'diced|minced|grated|finely\s+grated|julienned|'
    r'frozen|drained|rinsed|soaked|squeezed|'
    r'peeled|pitted|trimmed|zested|shredded|crumbled|'
    r'roasted|toasted|blended|mashed|beaten)'
    r'.*$',
    re.IGNORECASE,
)
_TRAILING_FOR_RE      = re.compile(r'\s+for\s+\w+.*$', re.IGNORECASE)
_TRAILING_TO_RE       = re.compile(r'\s+to\s+\w+.*$', re.IGNORECASE)
_PART_RE              = re.compile(r'\s+(?:floret|stalk|stem)s?\s*$', re.IGNORECASE)
_SECTION_RE           = re.compile(
    r'^(?:for\s+the|to\s+serve|to\s+garnish|for\s+garnish'
    r'|for\s+topping|note[:\s]|tip[:\s]|---)',
    re.IGNORECASE,
)

_WITH_LIQUID_RE    = re.compile(                               # "chickpeas with liquid/aquafaba/brine"
    r'\s+with\s+(?:the\s+)?(?:liquid|aquafaba|brine|juice|salt|oil|water)\b.*$',
    re.IGNORECASE,
)
_DASH_CLAUSE_RE    = re.compile(r'\s+[-–]\s+\w+.*$')           # "nut butter – improves texture"
_PAGE_REF_RE       = re.compile(r'\s+[-–]?\s*(?:see\s+)?(?:page|pg|p)\.*\s*\d+.*$', re.IGNORECASE)
_COMPOUND_SPLIT_RE = re.compile(r',\s*(?:and\s+)?|\s+and\s+', re.IGNORECASE)
_SIMPLE_AND_RE     = re.compile(r'^([\w][\w-]*)\s+and\s+([\w][\w-]*)$', re.IGNORECASE)

# Explicit singularization overrides for words where the generic rules produce wrong results
_SINGULAR_MAP: dict[str, str] = {
    'peaches':   'peach',
    'molasses':  'molasses',
    'oats':      'oat',
    'dates':     'date',
}


def _singularize(s: str) -> str:
    """Best-effort English singularization applied to the last word."""
    words = s.split()
    if not words:
        return s
    last = words[-1]
    lo = last.lower()
    if lo in _SINGULAR_MAP:
        last = _SINGULAR_MAP[lo]
    elif lo.endswith('ies') and len(last) > 4:
        last = last[:-3] + 'y'                           # cherries → cherry
    elif lo.endswith('ves') and len(last) > 4:
        if lo.endswith(('ives', 'oves')):
            last = last[:-1]                              # chives→chive, olives→olive, cloves→clove
        else:
            last = last[:-3] + 'f'                       # leaves→leaf, halves→half
    elif lo.endswith('oes') and len(last) > 5:
        last = last[:-2]                                  # tomatoes → tomato
    elif (lo.endswith('s')
          and not lo.endswith(('ss', 'us', 'is', 'ous', 'news', 'ics'))
          and len(last) > 3):
        last = last[:-1]                                  # eggs → egg, carrots → carrot
    words[-1] = last
    return ' '.join(words)


def norm_ingredient(raw: str) -> str:
    s = raw.strip()
    # Drop section headers / instructional lines entirely
    if _SECTION_RE.match(s):
        return ''
    s = _OPTIONAL_RE.sub('', s)
    s = _JUICE_ZEST_RE.sub('', s)      # "juice of a lemon" → "lemon"
    s = _PLUS_RE.sub('', s)
    s = _PAREN_RE.sub('', s).strip()
    s = _DASH_CLAUSE_RE.sub('', s)     # strip "– adds flavour", "– see page 286"
    s = _PAGE_REF_RE.sub('', s)        # strip "See page 286"
    s = _INDEF_RE.sub('', s)
    s = _COLLOQUIAL_QTY_RE.sub('', s)
    s = _QTY_RE.sub('', s)
    s = _OF_RE.sub('', s)
    s = _QTY_RE.sub('', s)             # second pass: catches "0% sugar", "85% dark choc"
    s = _COLLOQUIAL_QTY_RE.sub('', s)  # second pass: catches "tin/bulb/ball" exposed by QTY strip
    s = _OF_RE.sub('', s)
    s = _ARTICLE_RE.sub('', s)
    # Strip stacked prep/size adjectives (loop until stable)
    for _ in range(8):
        s = _PLUS_RE.sub('', s)        # re-strip & exposed by prior prep removal
        s = _COLLOQUIAL_QTY_RE.sub('', s)  # catch container words exposed by prep strip
        s = _OF_RE.sub('', s)
        stripped = _PREP_RE.sub('', s)
        if stripped == s:
            break
        s = stripped
    s = _OR_ALT_RE.sub('', s)
    s = _WITH_LIQUID_RE.sub('', s)
    s = _TRAILING_COMMA_PREP_RE.sub('', s)
    s = _TRAILING_FOR_RE.sub('', s)
    s = _OF_CHOICE_RE.sub('', s)
    s = _TRAILING_TO_RE.sub('', s)
    s = _PART_RE.sub('', s)
    s = s.strip(' ,.-–')
    if len(s.split()) >= 6:
        return ''
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
