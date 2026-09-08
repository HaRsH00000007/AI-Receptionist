"""Versioned prompt templates, loaded from files.

Prompts live as files rather than string literals so they can be edited and
diffed like the product surface they are. The filename carries the version, and
that version is stored on every ``agent_configs`` row — which is what makes
"re-render every tenant still on v1" a query rather than an archaeology
exercise.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

PROMPT_DIR = Path(__file__).resolve().parent

#: Bump by adding a new file, never by editing one in place: an edited template
#: would silently change what a recorded version means.
AGENT_CONFIG_PROMPT = "agent_config.v1"
CALL_SUMMARY_PROMPT = "call_summary.v1"


@lru_cache(maxsize=16)
def load_prompt(name: str) -> str:
    """Read a prompt file by version name, e.g. ``agent_config.v1``."""
    path = PROMPT_DIR / f"{name}.md"
    if not path.is_file():
        raise FileNotFoundError(f"prompt template not found: {path}")
    return path.read_text(encoding="utf-8").strip()


def split_sections(text: str) -> tuple[str, str]:
    """Split a template into its system and user halves.

    Templates keep both in one file so a change to the instructions and the
    input format cannot drift apart. The separator is a lone ``---`` line.
    """
    system, separator, user = text.partition("\n---\n")
    if not separator:
        raise ValueError("prompt template must contain a '---' separator line")
    return system.strip(), user.strip()
