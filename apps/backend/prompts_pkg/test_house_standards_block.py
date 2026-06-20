"""Tests for the RFC-0012 house-testing-standards planner block (#138)."""

from __future__ import annotations

import json
from pathlib import Path

from prompts_pkg.prompts import _build_house_standards_block


def _write_contract(spec_dir: Path, contract: dict) -> None:
    ctx = spec_dir / "context"
    ctx.mkdir(parents=True, exist_ok=True)
    (ctx / "task_contract.json").write_text(json.dumps(contract))


def _standards(available: bool = True) -> dict:
    return {
        "contract_version": "2",
        "epic_context": {
            "house_standards": {
                "available": available,
                "sources": [
                    {
                        "source": "baseline",
                        "kind": "conventions",
                        "conventions": {
                            "code_quality_tools": ["ruff", "pytest"],
                            "test_layout": "tests/",
                        },
                        "content_hash": "sha256:abc",
                    },
                    {
                        "source": "backstage",
                        "kind": "component",
                        "techdocs_refs": ["dir:./docs/testing.md"],
                        "lifecycle": "production",
                        "content_hash": "sha256:def",
                    },
                ],
            }
        }
    }


def test_renders_block(tmp_path: Path):
    _write_contract(tmp_path, _standards())
    block = _build_house_standards_block(tmp_path)
    assert "## HOUSE TESTING STANDARDS" in block
    assert "ruff, pytest" in block
    assert "tests/" in block
    assert "production" in block
    assert "dir:./docs/testing.md" in block


def test_no_contract_returns_empty(tmp_path: Path):
    assert _build_house_standards_block(tmp_path) == ""


def test_unavailable_returns_empty(tmp_path: Path):
    _write_contract(tmp_path, _standards(available=False))
    assert _build_house_standards_block(tmp_path) == ""


def test_no_sources_returns_empty(tmp_path: Path):
    _write_contract(tmp_path, {"contract_version": "2", "epic_context": {"house_standards": {"available": True, "sources": []}}})
    assert _build_house_standards_block(tmp_path) == ""
