"""Factory#3774: the work-item identifier is a string at the provider boundary."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).parent.parent
for _p in (_ROOT / "apps" / "backend", _ROOT / "apps" / "web-server"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from integrations.pfactory.pickup import classify_issue  # noqa: E402
from runners.github.providers.protocol import as_issue_int  # noqa: E402

_SCHEMA = _ROOT / "apps/backend/contracts/task-contract-v2.schema.json"


def test_schema_accepts_integer_and_string_issue_number() -> None:
    schema = json.loads(_SCHEMA.read_text())
    prop = schema["properties"]["provenance"]["properties"]["issue_number"]
    assert set(prop["type"]) == {"integer", "string"}


def test_as_issue_int_numeric_and_key() -> None:
    assert as_issue_int("5") == 5
    assert as_issue_int(5) == 5
    with pytest.raises(ValueError, match="PROJ-1"):
        as_issue_int("PROJ-1")


def test_pickup_reads_a_decimal_string_number() -> None:
    """IssueData.number is a str now; the pickup must not drop it."""
    out = classify_issue({"labels": ["tfactory"], "number": "12", "body": ""})
    assert out.issue_number == 12


def test_no_integer_equality_against_issue_identifiers() -> None:
    """An int compared to a str identifier is silently never equal."""
    pat = re.compile(r"\b(issue_number|issue_id|\.number)\s*[!=]=\s*\d")
    hits = []
    for base in ("apps/backend", "apps/web-server/server"):
        for path in (_ROOT / base).rglob("*.py"):
            if ".venv" in path.parts or "providers" in path.parts:
                continue
            for n, line in enumerate(path.read_text().splitlines(), 1):
                if pat.search(line):
                    hits.append(f"{path.relative_to(_ROOT)}:{n}: {line.strip()}")
    assert not hits, "\n".join(hits)


def test_existing_issue_numbers_match_issue_data_strings(tmp_path: Path) -> None:
    from server.services.auto_fix_service import _existing_issue_numbers

    (tmp_path / ".tfactory" / "specs" / "001-gh007-fix").mkdir(parents=True)
    existing = _existing_issue_numbers(tmp_path)
    assert "7" in existing  # IssueData.number == "7" is found, not re-imported
