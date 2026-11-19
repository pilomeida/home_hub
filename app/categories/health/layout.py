"""The paper's structure, read with code (PLAN step 5d): what the PDF itself says about headings and printed
references, so the model only interprets and code settles what is printed.

From the PDF's text runs (position, font, size) the lines of each page are rebuilt. A line in bold, larger type is a
section ("HEMATOLOGIA"); a line with a label and nothing else on it is a heading ("Hemoglobina glicada A1c"); a line
with a label and figures is a result. Two corrections follow, both only where the paper settles it:
- a result printed under a heading is placed under it when the reading left it directly under the section, or
  gave it no place under that heading while its name is generic ("Resultado (IFCC)", "Doseamento");
- a reference the reading changed ("=< 15,0" read as ">= 15,0") is replaced by the reference printed on the result's
  own line, when the figures are the same.
A scan or photo has no text runs: nothing is changed."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from app.categories.health.text import PATH_SEP, limits_from_reference, match_key, spaced_intervals

_FIGURE = re.compile(r"\d")
_REFERENCE = re.compile(r"(?:[<>=≤≥]{1,2}\s*\d+(?:[.,]\d+)?|\d+(?:[.,]\d+)?\s*[-–]\s*\d+(?:[.,]\d+)?)")
_LABEL_X = 80            # a label starts at the left margin
_SECTION_SIZE = 8.5      # bold at least this size is a section heading
_GENERIC = ("resultado", "doseamento", "valor", "result", "value")
_METHOD = re.compile(r"\(\s*(?:m[ée]t|method)[^)]*\)", re.IGNORECASE)
_VALUES_X = 200          # results, units and references are printed right of this
_NUMBER = re.compile(r"(?<![A-Za-zÀ-ÿ])\d+(?:[.,]\d+)?(?![A-Za-zÀ-ÿ])")     # "A1c" is a name, "5,3" a figure
_LETTER = re.compile(r"[A-Za-zÀ-ÿ]{3}")
_SAME_LINE = 4.0         # runs whose baselines are this close are one printed line (a smaller font sits lower)


@dataclass
class Run:
    x: float
    size: float
    bold: bool
    text: str


@dataclass
class Line:
    page: int
    y: float
    runs: list[Run] = field(default_factory=list)

    @property
    def text(self) -> str:
        return " ".join(r.text for r in sorted(self.runs, key=lambda r: r.x))

    @property
    def label(self) -> str:
        left = [r for r in sorted(self.runs, key=lambda r: r.x) if r.x < _LABEL_X and not _METHOD.fullmatch(r.text)]
        return left[0].text if left else ""

    @property
    def is_section(self) -> bool:
        return any(r.bold and r.size >= _SECTION_SIZE for r in self.runs) and not _NUMBER.search(self.text)

    @property
    def is_heading(self) -> bool:
        """A label at the left margin with nothing in the value columns and no figure once its method in brackets
        is set aside ("HEMOGLOBINA GLICADA A1c ( Mét.Electrof. capilar )")."""
        if self.is_section or not self.runs or min(r.x for r in self.runs) >= _LABEL_X:
            return False
        if any(r.x >= _VALUES_X for r in self.runs):
            return False
        words = _METHOD.sub("", self.text).strip()
        return bool(_LETTER.search(words)) and not _NUMBER.search(words)


def read_lines(file_path: str) -> list[Line]:
    """The paper's lines, page by page, top to bottom; [] when it has no text runs (a scan) or cannot be read."""
    if not file_path.lower().endswith(".pdf"):
        return []
    try:
        from pypdf import PdfReader
        reader = PdfReader(file_path)
        out: list[Line] = []
        for number, page in enumerate(reader.pages):
            found: list[tuple[float, Run]] = []
            last: list[tuple[float, float]] = []            # where the latest text operator drew (pypdf sometimes
                                                             # reports a run's position as 0,0: this one is right)

            def before(op, args, cm, tm, last=last):
                if op in (b"Tj", b"TJ", b"'", b'"') and tm:
                    last[:] = [(tm[4], tm[5])]

            def visit(text, cm, tm, font, size, found=found, last=last):
                text = (text or "").strip()
                if not text or not tm:
                    return
                x, y = tm[4], tm[5]
                if (x, y) == (0, 0) and last:
                    x, y = last[0]
                name = (font or {}).get("/BaseFont", "") if isinstance(font, dict) else ""
                found.append((y, Run(x, abs(size * tm[0]) if tm[0] else size, "bold" in str(name).lower(), text)))
            page.extract_text(visitor_text=visit, visitor_operand_before=before)
            for y, run in sorted(found, key=lambda f: -f[0]):              # top to bottom
                if out and out[-1].page == number and out[-1].y - y <= _SAME_LINE:
                    out[-1].runs.append(run)
                else:
                    out.append(Line(number, y, [run]))
        return out
    except Exception:              # a damaged PDF: no structure, the reading stands as it is
        return []


def _context(lines: list[Line]) -> list[tuple[str, str]]:
    """For each line: the section and the heading it is printed under ("" when none)."""
    section, heading, out = "", "", []
    for line in lines:
        if line.is_section:
            section, heading = line.text, ""
        elif line.is_heading:
            heading = _METHOD.sub("", line.text).strip()
        out.append((section, heading))
    return out


def _same(a: str, b: str) -> bool:
    return bool(a) and bool(b) and match_key(a) == match_key(b)


def _figures(text: str) -> list[str]:
    return re.findall(r"\d+(?:[.,]\d+)?", text or "")


def _spelling(x, printed: str) -> str:
    """The reading's own spelling of a heading when it has one; else the printed one, not in capitals."""
    for m in x.measurements:
        for part in (m.group_path or "").split(PATH_SEP):
            if _same(part, printed):
                return part
    letters = [c for c in printed if c.isalpha()]
    if not letters or sum(c.isupper() for c in letters) < 0.8 * len(letters):
        return printed
    words = [w if any(c.isdigit() for c in w) else w.lower() for w in printed.split()]     # "A1c" stays as printed
    return " ".join(words)[:1].upper() + " ".join(words)[1:]


def apply_layout(x, file_path: str) -> list[str]:
    """Correct the reading `x` (ExtractedHealth) where the paper's own layout settles it. Returns what was changed,
    in plain words (for the log); [] when nothing."""
    lines = read_lines(file_path)
    if not lines:
        return []
    context = _context(lines)
    changed: list[str] = []
    start = 0
    for m in x.measurements:
        if m.is_previous:
            continue
        name = re.sub(r"\s*\(.*?\)\s*$", "", m.test).strip()
        at = next((i for i in range(start, len(lines)) if _same(lines[i].label, name)
                   or match_key(lines[i].label).startswith(match_key(name) + " ")), None)
        if at is None:
            continue
        start = at + 1
        section, heading = context[at]
        parts = [p for p in (m.group_path or "").split(PATH_SEP) if p]
        generic = match_key(name).split(" ")[0] in _GENERIC
        only_section = len(parts) == 1 and _same(parts[0], section)
        if heading and (generic or only_section) and not any(_same(p, heading) for p in parts):
            before = m.group_path
            named = _spelling(x, heading)
            m.group_path = PATH_SEP.join(parts[:1] + [named]) if parts else named
            changed.append(f"{m.test}: placed under {m.group_path} (was {before or 'no heading'}), as printed")
        printed = [spaced_intervals(r) or "" for r in _REFERENCE.findall(lines[at].text)]
        ours = spaced_intervals(m.reference_text) or ""
        flat = " ".join(lines[at].text.split())
        if ours and printed and " ".join(ours.split()) not in flat:
            same = next((r for r in printed if r != ours and _figures(r) == _figures(ours)), None)
            if same is not None:
                changed.append(f"{m.test}: reference {ours} corrected to {same}, as printed")
                m.reference_text = same
                m.ref_low, m.ref_high = limits_from_reference(same)
    return changed


__all__ = ["read_lines", "apply_layout", "Line", "Run"]
