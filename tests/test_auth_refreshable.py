"""Tests for prefer_refreshable_credentials (verify-agent 401 fix, #666).

A static CLAUDE_CODE_OAUTH_TOKEN never refreshes, so once it expires the in-pod
verify/plan agent 401s. When ~/.claude/.credentials.json carries a refreshToken
the SDK can renew the access token itself, so the static env token must be
scrubbed at spawn time to let the SDK own auth.
"""

import json
import os

from core.auth import prefer_refreshable_credentials


def _write_creds(home, *, refresh_token=True):
    d = home / ".claude"
    d.mkdir(parents=True, exist_ok=True)
    oauth = {"accessToken": "sk-ant-oat01-expired"}
    if refresh_token:
        oauth["refreshToken"] = "sk-ant-ort01-valid"
    (d / ".credentials.json").write_text(json.dumps({"claudeAiOauth": oauth}))


def test_scrubs_env_when_refresh_token_present(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "sk-ant-oat01-expired")
    _write_creds(tmp_path, refresh_token=True)

    assert prefer_refreshable_credentials() is True
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in os.environ


def test_keeps_env_without_refresh_token(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "sk-ant-oat01-static")
    _write_creds(tmp_path, refresh_token=False)

    assert prefer_refreshable_credentials() is False
    assert os.environ.get("CLAUDE_CODE_OAUTH_TOKEN") == "sk-ant-oat01-static"


def test_noop_when_no_creds_file(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "sk-ant-oat01-static")

    assert prefer_refreshable_credentials() is False
    assert os.environ.get("CLAUDE_CODE_OAUTH_TOKEN") == "sk-ant-oat01-static"
