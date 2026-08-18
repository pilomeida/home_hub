"""Shared helper for parsing JSON out of Claude responses."""

import re

_FENCE_RE = re.compile(r"^```(?:json)?\s*\n?(.*?)\n?```$", re.DOTALL)


def strip_json_fences(text: str) -> str:
    """Strip a surrounding markdown code fence (```` ```json ... ``` ````)
    from a Claude response, if present. Claude sometimes wraps JSON in a
    fence despite being asked for raw JSON; this makes json.loads robust
    to that without changing the happy-path (unfenced) case."""
    stripped = text.strip()
    match = _FENCE_RE.match(stripped)
    return match.group(1).strip() if match else stripped
