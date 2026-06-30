"""Shared ingredient normalization helpers and harmonization I/O."""

import json
import re
from pathlib import Path

_HARM_FILE = Path("data/ingredient_harmonization.json")

# ── Normalization regexes ─────────────────────────────────────────────────────

_PLUS_RE        = re.compile(r'^[+&/]\s*')  # strip leading +, & or / (/ appears after prep-word strip)
_MARKDOWN_RE    = re.compile(r'^[*_~`]+|[*_~`]+$')  # strip * _ ~ ` formatting marks
_INLINE_VEGAN_RE = re.compile(r'\bvegan\b\s*', re.IGNORECASE)  # strip "vegan" anywhere
_INLINE_DIET_RE  = re.compile(   # strip diet labels from anywhere in the name
    r'\b(?:sugar-?free|dairy-?free|fat-?free|oil-?free|gluten-?free)\b\s*',
    re.IGNORECASE,
)
_PLUS_QTY_RE   = re.compile(r'\s+\+\s+\d.*$')  # strip "+ 1 egg white" addons
_OPTIONAL_RE    = re.compile(
    r'^\(optional\)\s*|^optional\s*[-–:]\s*|\s*,\s*optional\s*$',
    re.IGNORECASE,
)
# Spelling normalizations applied early (abbreviations + regional variants)
_CHOC_RE    = re.compile(r'\bchoc\b', re.IGNORECASE)      # "choc chip" → "chocolate chip"
_YOGHURT_RE = re.compile(r'\byoghurt\b', re.IGNORECASE)   # "yoghurt" → "yogurt"
_SOYA_RE    = re.compile(r'\bsoya\b', re.IGNORECASE)       # "soya sauce" → "soy sauce"
_PAREN_RE       = re.compile(r'\s*\([^)]*\)')
_UNCLOSED_PAREN_RE = re.compile(r'\s*\([^)]*$')  # strip unclosed parens: "Oats (or 30g oat"
_JUICE_ZEST_RE  = re.compile(                 # "juice of a lemon" → "lemon"
    r'^(?:the\s+)?(?:juice|zest)\s+of\s+'
    r'(?:[½¼\d/]+\s+)?(?:a\s+|an\s+|one\s+|half\s+(?:a\s+)?)?',
    re.IGNORECASE,
)
_QTY_RE = re.compile(
    r'^[½¼¾⅓⅔⅛⅜⅝⅞\d./\s%+\-–]+'           # -–: covers "1-2 cups" range quantities
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
    r'bag(?:s)?|clove(?:s)?|spoonful(?:s)?|inch(?:es)?)\s+(?:of\s+)?',
    re.IGNORECASE,
)
_OF_CHOICE_RE   = re.compile(r'\s+of\s+(?:\w+\s+)?choice\s*$', re.IGNORECASE)  # "of choice", "of your choice"
_INDEF_RE       = re.compile(
    r'^(?:'
    r'a\s+few\s+\w+\s+|a\s+handful\s+|some\s+|an?\s+'  # indefinite articles/quantifiers
    r'|few\s+'                              # "few jalapeños" (without leading "a")
    r'|your\s+favou?rite\s+|my\s+'          # possessives
    r'|half\s+an?\s+'                       # "half a lime", "half an avocado"
    r'|whole\s+lot\s+of\s+'                 # "whole lot of potato"
    r'|(?:two|three|four|five|six|seven|eight|nine|ten)\s+'  # written numbers
    r')(?:of\s+)?',
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
    r'blended|pur[eé]ed|mashed|crumbled|strained|sifted|whisked|beaten|melted|'
    r'brewed|soaked|tinned|reduced|whipped|spreadable|'
    # treatment
    r'peeled|pitted|seeded|deseeded|destemmed|halved|quartered|'
    r'skinless|boneless|lean|skinned|trimmed|deboned|'
    r'washed|drained|rinsed|squeezed|zested|salted|unsalted|seasoned|'
    # size / shape / quality descriptor
    r'large|medium|small|big|fat|tiny|giant|mini|thick|thin|bite-?sized?|huge|fine|yellow|'
    r'julienned|'
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
    r'nice|good|great|beautiful|perfect|additional|homemade|favou?rite|'
    # temperature / texture / state (SAFE: no known compound names start with these)
    r'chilled|warm|lukewarm|very|juicy|squishy|'
    r'crunchy|chunky|smooth|creamy|crispy|crisp|'
    # age / maturity
    r'baby|young|aged|mature|old|'
    # misc leading qualifiers
    r'extra|plain|natural|pure|simple|easy|standard|'
    r')(?:\s+|/|$)',   # "/" covers "shredded/minced" → strips "shredded/" → next pass strips "minced "
    re.IGNORECASE,
)

_OR_ALT_RE            = re.compile(r'\s*,?\s+(?:or\s+|/\s*).+$', re.IGNORECASE)
_TRAILING_COMMA_SHAPE_RE = re.compile(  # ", thin round slices", ", small cubes" — shape instructions
    r'\s*,\s*(?:(?:thin|thick|large|small|flat|round|long|diagonal)\s+)*'
    r'(?:slice|strip|cube|chunk|dice|ring|half|quarter|piece)s?\s*$',
    re.IGNORECASE,
)
_TRAILING_COMMA_PREP_RE = re.compile(  # ", chopped", ", drained & rinsed", ", no added sugar", etc.
    r'\s*,\s*(?:[+&]\s*)?'
    r'(?:halved|sliced|thinly\s+sliced|finely\s+sliced|'
    r'chopped|finely\s+chopped|roughly\s+chopped|coarsely\s+chopped|'
    r'diced|minced|grated|finely\s+grated|julienned|'
    r'frozen|drained|rinsed|soaked|squeezed|'
    r'peeled|pitted|trimmed|zested|shredded|crumbled|shred(?:ded)?|'
    r'roasted|toasted|blended|mashed|beaten|melted|ground|cooked|spiralized|'
    r'no\s+added\s+\w+|sugar-?free|fat-?free|dairy-?free|oil-?free|gluten-?free)'
    r'.*$',
    re.IGNORECASE,
)
_TRAILING_FOR_RE      = re.compile(r'\s+for\s+\w+.*$', re.IGNORECASE)
_TRAILING_TO_RE       = re.compile(r'\s+(?:to|as)\s+\w+.*$', re.IGNORECASE)  # "to taste", "as needed"
_TRAILING_ON_RE       = re.compile(r'\s+on\s+(?:the\s+)?top\b.*$', re.IGNORECASE)
_TRAILING_INTO_RE     = re.compile(r'\s+(?:cut|torn|broken|divided|sliced)\s+into\s+.*$', re.IGNORECASE)
_TRAILING_BARE_PREP_RE = re.compile(
    r'\s+(?:chopped|sliced|diced|minced|grated|peeled|frozen|drained|rinsed|'
    r'soaked|washed|blended|mashed|trimmed|shredded|crumbled|roasted|toasted|'
    r'beaten|squeezed|crushed|ground|sifted|whipped|brewed|tinned|pickled|'
    r'smoked|cured|caramelized|baked|steamed|boiled|fried|poached|grilled|'
    r'halved|quartered|julienned|zested|deseeded|seeded|pitted|destemmed|'
    r'melted|spiralized|cooked|pureed|pur[eé]ed|strained|juiced)$',
    re.IGNORECASE,
)
_PART_RE              = re.compile(
    r'\s+(?:floret|stalk|stem|piece|fillet|loin|drizzle|sprinkle)s?\s*$',
    re.IGNORECASE,
)
_LEADING_OR_QTY_RE    = re.compile(  # "1 cup or 226g cottage cheese" → after "1 cup" stripped, catches "or 226g "
    r'^or\s+[½¼¾⅓⅔⅛⅜⅝⅞\d./]+\s*'
    r'(?:g|ml|mL|dl|dL|l|L|kg|lb|lbs|oz|tbsp|tsp|tablespoons?|teaspoons?|cups?)?\b\s*',
    re.IGNORECASE,
)
_SECTION_RE           = re.compile(
    r'^(?:for\s+the|to\s+serve|to\s+garnish|for\s+garnish'
    r'|for\s+topping|note[:\s]|tip[:\s]|---|or\s+|filling\s+ideas)',
    re.IGNORECASE,
)

_WITH_LIQUID_RE    = re.compile(                               # "chickpeas with liquid/aquafaba/brine"
    r'\s+with\s+(?:the\s+)?(?:\w+\s+)?(?:liquid|aquafaba|brine|juice|salt|oil|water)\b.*$',
    re.IGNORECASE,
)
_DASH_CLAUSE_RE    = re.compile(r'\s+[-–]\s+\w+.*$')           # "nut butter – improves texture"
_PAGE_REF_RE       = re.compile(r'\s+[-–]?\s*(?:see\s+)?(?:page|pg|p)\.*\s*\d+.*$', re.IGNORECASE)
_COMPOUND_SPLIT_RE = re.compile(r',\s*(?:and\s+)?|\s+and\s+', re.IGNORECASE)
_SIMPLE_AND_RE     = re.compile(r'^([\w][\w-]*)\s+and\s+([\w][\w-]*)$', re.IGNORECASE)

# Herb names where trailing "leaf/leave/leaves" is redundant (basil leaf → basil)
_HERB_NAMES = frozenset([
    'basil', 'mint', 'cilantro', 'parsley', 'sage', 'thyme', 'oregano',
    'rosemary', 'coriander', 'dill', 'chervil', 'tarragon', 'lovage',
])
# Nut base words where trailing "nut" is redundant (cashew nut → cashew)
_NUT_BASE_WORDS = frozenset([
    'cashew', 'almond', 'pistachio', 'macadamia', 'pecan', 'brazil', 'pine', 'hazel',
])

# Explicit singularization overrides for words where the generic rules produce wrong results
_SINGULAR_MAP: dict[str, str] = {
    'peaches':   'peach',
    'molasses':  'molasses',
    'oats':      'oat',
    'dates':     'date',
    'veggies':   'veggie',   # "veggies" → "veggie" (generic rule gives wrong "veggy")
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
    s = _MARKDOWN_RE.sub('', s).strip()     # strip * _ ~ ` formatting marks
    if not s:
        return ''
    # Spelling/abbreviation normalizations (must happen before all other rules)
    s = _CHOC_RE.sub('chocolate', s)        # "choc" → "chocolate"
    s = _YOGHURT_RE.sub('yogurt', s)        # "yoghurt" → "yogurt"
    s = _SOYA_RE.sub('soy', s)              # "soya" → "soy"
    # Drop section headers / instructional / alternative lines entirely
    if _SECTION_RE.match(s):
        return ''
    s = _OPTIONAL_RE.sub('', s)
    s = _JUICE_ZEST_RE.sub('', s)           # "juice of a lemon" → "lemon"
    s = _INLINE_VEGAN_RE.sub('', s).strip() # strip "vegan" wherever it appears
    s = _INLINE_DIET_RE.sub('', s).strip()  # strip "sugar-free", "dairy-free" etc. from middle of names
    s = _PLUS_RE.sub('', s)
    s = _PAREN_RE.sub('', s).strip()
    s = _UNCLOSED_PAREN_RE.sub('', s).strip()
    s = _DASH_CLAUSE_RE.sub('', s)
    s = _PAGE_REF_RE.sub('', s)
    s = _INDEF_RE.sub('', s)
    s = _COLLOQUIAL_QTY_RE.sub('', s)
    s = _QTY_RE.sub('', s)
    s = _OF_RE.sub('', s)
    s = _QTY_RE.sub('', s)             # second pass: catches "0% sugar", "85% dark choc"
    s = _COLLOQUIAL_QTY_RE.sub('', s)  # second pass: catches "tin/bulb/ball" exposed by QTY strip
    s = _OF_RE.sub('', s)
    s = _PLUS_QTY_RE.sub('', s)        # strip "+ 1 egg white" (after qty already consumed fractions)
    s = _LEADING_OR_QTY_RE.sub('', s) # strip "or 226g" qty alternative exposed after QTY strip
    s = _QTY_RE.sub('', s)            # third pass: catch qty exposed by leading-or-qty strip
    s = _JUICE_ZEST_RE.sub('', s)     # second pass: "1 zest of lemon" — "1" stripped above, now catches "zest of"
    s = _ARTICLE_RE.sub('', s)
    s = _INDEF_RE.sub('', s)          # second pass: catches "my/your favourite" exposed by qty stripping
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
    s = _TRAILING_INTO_RE.sub('', s)        # "chicken breast cut into strips" → "chicken breast"
    s = _TRAILING_COMMA_SHAPE_RE.sub('', s)  # ", thin round slices" — shape instructions
    s = _TRAILING_COMMA_PREP_RE.sub('', s)
    s = _TRAILING_BARE_PREP_RE.sub('', s)   # "coriander chopped" → "coriander"
    s = _TRAILING_FOR_RE.sub('', s)
    s = _TRAILING_ON_RE.sub('', s)          # "peanuts on top" → "peanuts"
    s = _OF_CHOICE_RE.sub('', s)
    s = _TRAILING_TO_RE.sub('', s)
    s = _PART_RE.sub('', s)
    s = s.strip(' ,.-–*')
    # Word-level: strip trailing "leaf/leave/leaves" from herb names
    words_lo = s.lower().split()
    if len(words_lo) >= 2 and words_lo[-1] in ('leaf', 'leave', 'leaves') and words_lo[-2] in _HERB_NAMES:
        s = ' '.join(s.split()[:-1]).strip()
        words_lo = s.lower().split()
    # Word-level: strip redundant "nut" suffix from specific nut types
    if len(words_lo) >= 2 and words_lo[-1] in ('nut', 'nuts') and words_lo[-2] in _NUT_BASE_WORDS:
        s = ' '.join(s.split()[:-1]).strip()
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
