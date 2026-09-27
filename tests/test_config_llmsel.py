"""Config and requirements no longer hold a provider key: the gateway's
LLMSEL_* variables replace ANTHROPIC_API_KEY everywhere."""

import os
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent


def test_settings_have_llmsel_vars():
    from app.config import Settings

    assert hasattr(Settings, "LLMSEL_URL")
    assert hasattr(Settings, "LLMSEL_WORKER")
    assert hasattr(Settings, "LLMSEL_TOKEN")
    s = Settings()
    assert s.LLMSEL_WORKER == "hub"


def test_settings_no_anthropic_api_key():
    from app.config import Settings

    assert not hasattr(Settings, "ANTHROPIC_API_KEY")
    assert "ANTHROPIC_API_KEY" not in Settings.__dict__


def test_no_required_env_var_without_default_removed():
    # importing app.config must succeed without ANTHROPIC_API_KEY set
    env = os.environ.copy()
    env.pop("ANTHROPIC_API_KEY", None)
    import subprocess
    import sys

    proc = subprocess.run(
        [sys.executable, "-c", "from app.config import settings; print(settings.LLMSEL_URL)"],
        env={k: v for k, v in env.items()},
        capture_output=True, text=True, cwd=REPO,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr


def test_env_example_has_no_anthropic_key():
    text = (REPO / ".env.example").read_text()
    assert "ANTHROPIC_API_KEY" not in text
    assert "LLMSEL_URL" in text
    assert "LLMSEL_WORKER" in text
    assert "LLMSEL_TOKEN" in text


def test_requirements_have_no_anthropic_package():
    text = (REPO / "requirements.txt").read_text()
    assert not re.search(r"^\s*anthropic", text, re.MULTILINE)
