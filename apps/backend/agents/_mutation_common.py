"""Shared, language-agnostic mutation scaffolding for the per-language probes.

The generic 'mutate the first applicable line' loop, parameterised by a
language-specific ``mutate_line`` predicate so each lang_* probe keeps its own
mutation operators while sharing the iteration logic.
"""

from __future__ import annotations

from collections.abc import Callable


def mutate_first_assertion(
    source: str, mutate_line: Callable[[str], tuple[str, str] | None]
) -> tuple[str | None, str | None]:
    """Mutate the first line for which *mutate_line* applies. Returns (mutated_source, desc)."""
    lines = source.splitlines(keepends=True)
    for i, line in enumerate(lines):
        result = mutate_line(line)
        if result:
            mutated_line, desc = result
            # preserve the original line ending
            if line.endswith("\n") and not mutated_line.endswith("\n"):
                mutated_line += "\n"
            lines[i] = mutated_line
            return "".join(lines), desc
    return None, None
