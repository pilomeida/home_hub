from app.services.json_utils import strip_json_fences


def test_strip_json_fences_removes_json_language_fence():
    text = '```json\n{"a": 1}\n```'
    assert strip_json_fences(text) == '{"a": 1}'


def test_strip_json_fences_removes_bare_fence():
    text = '```\n{"a": 1}\n```'
    assert strip_json_fences(text) == '{"a": 1}'


def test_strip_json_fences_passes_through_unfenced_text():
    text = '{"a": 1}'
    assert strip_json_fences(text) == '{"a": 1}'


def test_strip_json_fences_handles_surrounding_whitespace():
    text = '  \n```json\n{"a": 1}\n```\n  '
    assert strip_json_fences(text) == '{"a": 1}'
