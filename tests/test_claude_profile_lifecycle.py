"""Claude-profile lifecycle: create -> activate -> switch — #676.

Replaces the profile workflow from ``apps/web-server/tests/test_e2e_workflows.py``,
which patched ``apps.web-server.server.routes.settings.CLAUDE_PROFILES_FILE`` — a
module path that cannot be imported (hyphen) naming a constant that no longer
exists. It died at patch resolution, so it never asserted anything, and nothing
in CI collected it to say so.

This lives in ``tests/`` because that is what CI runs. The seam is
``settings.get_profiles_file``, which every read and write routes through
(``load_profiles``/``save_profiles`` resolve it from module globals at call
time, so patching it once also redirects the sub-router that imported them).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_WEB_SERVER = Path(__file__).parent.parent / "apps" / "web-server"
if str(_WEB_SERVER) not in sys.path:
    sys.path.insert(0, str(_WEB_SERVER))

import pytest  # noqa: E402
from server.routes import settings as settings_routes  # noqa: E402
from server.routes import settings_claude_profiles as profiles_routes  # noqa: E402


@pytest.fixture
def profiles_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect profile storage at the single seam both load and save use."""
    target = tmp_path / "claude-profiles.json"
    monkeypatch.setattr(settings_routes, "get_profiles_file", lambda: target)
    # Activation syncs the token into the environment; keep that off the real env.
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "")
    return target


async def _create(name: str, token: str) -> str:
    """Create a profile via the real endpoint, returning its generated id."""
    result = await profiles_routes.save_claude_profile(
        profiles_routes.ClaudeProfile(
            name=name, email=f"{name}@example.com", token=token
        )
    )
    assert result["success"] is True, result
    return result["data"]["id"]


async def test_create_activate_and_switch_profiles(profiles_file: Path) -> None:
    """The lifecycle the deleted test claimed to cover, against today's API."""
    id_1 = await _create("Work Account", "sess-" + "x" * 40)

    stored = json.loads(profiles_file.read_text())
    assert [p["name"] for p in stored["profiles"]] == ["Work Account"]

    activated = await profiles_routes.set_active_claude_profile(
        profiles_routes.ActiveProfileRequest(profileId=id_1)
    )
    assert activated["success"] is True
    assert json.loads(profiles_file.read_text())["activeProfileId"] == id_1

    id_2 = await _create("Personal Account", "sk-ant-" + "y" * 40)
    assert len(json.loads(profiles_file.read_text())["profiles"]) == 2

    # The rate-limit path: switch the active profile and report both ends.
    switched = await settings_routes.retry_with_profile(
        settings_routes.RetryWithProfileRequest(
            profileId=id_2,
            reason="rate_limit",
            operationContext={"operation": "generate_ideation"},
        )
    )
    assert switched["success"] is True
    assert switched["previousProfileId"] == id_1
    assert switched["newProfileId"] == id_2
    assert json.loads(profiles_file.read_text())["activeProfileId"] == id_2


async def test_duplicate_profile_name_is_rejected(profiles_file: Path) -> None:
    """Name uniqueness is enforced on create, not just in the UI."""
    await _create("Work Account", "sess-" + "x" * 40)

    result = await profiles_routes.save_claude_profile(
        profiles_routes.ClaudeProfile(name="Work Account", token="sess-" + "z" * 40)
    )

    assert result["success"] is False
    assert "already in use" in result["error"]
    assert len(json.loads(profiles_file.read_text())["profiles"]) == 1


async def test_malformed_token_is_rejected(profiles_file: Path) -> None:
    """Tokens must look like Claude tokens; a bad one must not reach storage."""
    result = await profiles_routes.save_claude_profile(
        profiles_routes.ClaudeProfile(
            name="Bad Token", token="not-a-claude-token-but-long"
        )
    )

    assert result["success"] is False
    assert "Invalid token format" in result["error"]
    assert not profiles_file.exists()
