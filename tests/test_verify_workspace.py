#!/usr/bin/env python3
"""Tests for the packed verify workspace round trip (RFC-0017 step 3, TFactory #1159).

The acceptance for #1159 is deliberately written against the ARTIFACTS, not the
Job: a packed run that loses its evidence looks identical, at the Job level, to
one that worked. So every assertion here is on what actually lands in the object
store — exact keys, exact file bytes — and never on a function having been called.

The store is a real ``ArtifactStore`` with an in-memory fake S3 client bolted onto
its lazy ``_s3`` slot, so the vendored key layout, tar packing and traversal
guards are all exercised for real; only the network is fake.
"""

from __future__ import annotations

import io
import tarfile
from pathlib import Path
from typing import Any

import pytest
from agents import verify_workspace as vw
from tools.runners.artifact_store import ArtifactStore, StoreConfig

_ENDPOINT = "http://minio.invalid:9000"
_BUCKET = "factory-artifacts"


class _FakeS3:
    """Minimal in-memory stand-in for the boto3 S3 client surface used here."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def put_object(self, *, Bucket: str, Key: str, Body: bytes, **_extra: Any) -> None:  # noqa: N803 - boto3 kwarg names
        assert Bucket == _BUCKET
        self.objects[Key] = Body

    def get_object(self, *, Bucket: str, Key: str) -> dict[str, io.BytesIO]:  # noqa: N803 - boto3 kwarg names
        assert Bucket == _BUCKET
        return {"Body": io.BytesIO(self.objects[Key])}


@pytest.fixture
def fake_s3(monkeypatch: pytest.MonkeyPatch) -> _FakeS3:
    """Point ``_open_store`` at one shared in-memory bucket."""
    s3 = _FakeS3()

    def _open() -> tuple[ArtifactStore, StoreConfig]:
        cfg = StoreConfig(
            endpoint=_ENDPOINT,
            bucket=_BUCKET,
            access_key="k",
            secret_key="s",
            region="us-east-1",
        )
        store = ArtifactStore(cfg)
        store._s3 = s3  # noqa: SLF001 - inject the transport, keep the real layout
        return store, cfg

    monkeypatch.setattr(vw, "_open_store", _open)
    return s3


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _members(blob: bytes) -> set[str]:
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tar:
        return set(tar.getnames())


@pytest.fixture
def data_root(tmp_path: Path) -> Path:
    """A miniature of the real layout: a spec tree whose ``.worktree`` is a LINKED
    worktree of a base clone that sits beside it, not under it."""
    root = tmp_path / "data"
    clone = root / "workspaces" / "demo-repo"
    spec = root / "workspaces" / "proj-1" / "specs" / "spec-7"
    _write(clone / ".git" / "worktrees" / "spec-7" / "gitdir", "x\n")
    _write(clone / "src" / "app.py", "print('sut')\n")
    _write(spec / "status.json", '{"status": "planned"}\n')
    _write(spec / ".worktree" / "test_thing.py", "def test_x():\n    assert True\n")
    _write(
        spec / ".worktree" / ".git",
        f"gitdir: {clone / '.git' / 'worktrees' / 'spec-7'}\n",
    )
    return root


def test_pack_carries_the_spec_tree_and_its_base_clone(
    fake_s3: _FakeS3, data_root: Path
) -> None:
    """The clone is a SIBLING of the spec tree, so packing the spec alone would
    ship a worktree whose ``.git`` pointer resolves to nothing."""
    spec = data_root / "workspaces" / "proj-1" / "specs" / "spec-7"
    uri = vw.pack_for_dispatch(
        spec_dir=spec,
        project_dir=spec / ".worktree",
        data_root=str(data_root),
        job_id="job-1",
        correlation_key=42,
    )
    assert uri == (f"s3://{_BUCKET}/tfactory/42/job-1/workspace/workspace.tar.gz")
    names = _members(fake_s3.objects["tfactory/42/job-1/workspace/workspace.tar.gz"])
    assert "workspaces/proj-1/specs/spec-7/status.json" in names
    assert "workspaces/proj-1/specs/spec-7/.worktree/test_thing.py" in names
    assert "workspaces/demo-repo/src/app.py" in names


def test_pack_is_fail_open_without_an_object_store(
    monkeypatch: pytest.MonkeyPatch, data_root: Path
) -> None:
    """No endpoint -> None -> the caller keeps today's PVC co-mount."""
    monkeypatch.setattr(vw, "_open_store", lambda: None)
    spec = data_root / "workspaces" / "proj-1" / "specs" / "spec-7"
    assert (
        vw.pack_for_dispatch(
            spec_dir=spec,
            project_dir=spec / ".worktree",
            data_root=str(data_root),
            job_id="job-1",
        )
        is None
    )


def test_round_trip_carries_evidence_produced_inside_the_job(
    fake_s3: _FakeS3, data_root: Path, tmp_path: Path
) -> None:
    """Pack -> unpack into a fresh 'emptyDir' -> write evidence there -> push back
    -> unpack again. The evidence must be in the object the second unpack reads,
    with its exact bytes. This is the whole point of #1159."""
    spec = data_root / "workspaces" / "proj-1" / "specs" / "spec-7"
    uri = vw.pack_for_dispatch(
        spec_dir=spec,
        project_dir=spec / ".worktree",
        data_root=str(data_root),
        job_id="job-1",
        correlation_key=42,
    )
    assert uri is not None

    # The Job: an empty node-local dir, seeded only from the packed URI.
    workdir = tmp_path / "work"
    assert vw.restore_workspace(uri=uri, root=str(workdir)) == workdir
    in_job_spec = workdir / "workspaces" / "proj-1" / "specs" / "spec-7"
    assert in_job_spec.joinpath("status.json").read_text() == '{"status": "planned"}\n'

    # ...which then produces the evidence the verdict is made of.
    _write(in_job_spec / "status.json", '{"status": "triaged"}\n')
    _write(in_job_spec / "findings" / "verdicts.json", '[{"ac": 1, "pass": true}]')
    _write(in_job_spec / ".worktree" / "shots" / "login.png", "PNG-BYTES")
    vw.push_back_workspace(root=workdir, job_id="job-1", correlation_key=42)

    # The control plane's view afterwards: only what survived the pod.
    restored = tmp_path / "restored"
    vw.restore_workspace(uri=uri, root=str(restored))
    out = restored / "workspaces" / "proj-1" / "specs" / "spec-7"
    assert out.joinpath("status.json").read_text() == '{"status": "triaged"}\n'
    assert (
        out.joinpath("findings", "verdicts.json").read_text()
        == '[{"ac": 1, "pass": true}]'
    )
    assert out.joinpath(".worktree", "shots", "login.png").read_text() == "PNG-BYTES"


def test_restore_is_a_no_op_on_the_co_mounted_path() -> None:
    """No WORKSPACE_URI -> None and no side effects, so the PVC path is unchanged."""
    assert vw.restore_workspace(uri="", root="") is None


def test_restore_raises_when_the_workspace_cannot_be_fetched(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A packed Job that cannot fetch its workspace must not verify an empty tree."""
    monkeypatch.setattr(vw, "_open_store", lambda: None)
    with pytest.raises(RuntimeError, match="S3_ENDPOINT"):
        vw.restore_workspace(uri="s3://b/k", root=str(tmp_path))


def test_push_back_raises_rather_than_dropping_evidence(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Fail-open here would be the silent data loss the whole change is about."""
    monkeypatch.setattr(vw, "_open_store", lambda: None)
    with pytest.raises(RuntimeError, match="pushed back"):
        vw.push_back_workspace(root=tmp_path, job_id="job-1")


# -- the dispatch side: the URI must actually reach the Job -------------------


def _verify_manifest(**kw: Any) -> dict[str, Any]:
    from agents.verify_dispatch import VerifyJobConfig, build_verify_job_manifest

    cfg = VerifyJobConfig(
        job_id="job-1",
        image="ghcr.io/x/tfactory:1",
        spec_subpath="workspaces/proj-1/specs/spec-7",
        project_subpath="workspaces/proj-1/specs/spec-7/.worktree",
        repo_pvc="tfactory-data",
        nix_develop=False,
        **kw,
    )
    return build_verify_job_manifest(cfg)


def _pod(manifest: dict[str, Any]) -> dict[str, Any]:
    pod: dict[str, Any] = manifest["spec"]["template"]["spec"]
    return pod


def _env(manifest: dict[str, Any]) -> dict[str, str]:
    return {
        e["name"]: e.get("value", "")
        for e in _pod(manifest)["containers"][0].get("env", [])
    }


def test_packed_verify_job_takes_no_pvc_and_learns_where_to_unpack() -> None:
    uri = f"s3://{_BUCKET}/tfactory/42/job-1/workspace/workspace.tar.gz"
    manifest = _verify_manifest(workspace_uri=uri)
    repo = next(v for v in _pod(manifest)["volumes"] if v["name"] == "repo")
    # The PVC co-mount is the node pin; a packed Job must not have one even though
    # repo_pvc is still threaded (the mid-migration shape kube_sandbox guards).
    assert "persistentVolumeClaim" not in repo
    assert repo["emptyDir"] == {}
    env = _env(manifest)
    assert env["WORKSPACE_URI"] == uri
    # Without the root the pipeline cannot know where the absolute --spec path
    # lives, so it would unpack nowhere and verify an empty tree.
    assert env["WORKSPACE_ROOT"] == "/work"


def test_unpacked_verify_job_is_unchanged() -> None:
    manifest = _verify_manifest()
    repo = next(v for v in _pod(manifest)["volumes"] if v["name"] == "repo")
    assert repo["persistentVolumeClaim"]["claimName"] == "tfactory-data"
    assert "WORKSPACE_URI" not in _env(manifest)
    assert "WORKSPACE_ROOT" not in _env(manifest)


# -- the entrypoint: the Job must actually round-trip its own workspace --------


def _pack(data_root: Path) -> str:
    spec = data_root / "workspaces" / "proj-1" / "specs" / "spec-7"
    uri = vw.pack_for_dispatch(
        spec_dir=spec,
        project_dir=spec / ".worktree",
        data_root=str(data_root),
        job_id="job-1",
        correlation_key=42,
    )
    assert uri is not None
    return uri


def _lifecycle_of(row: dict[str, Any]) -> str:
    """The canonical state the durable store WILL record for this row.

    Loaded straight off disk because the sibling web-server app is not on the
    backend tests' sys.path; using the real mapper (rather than restating its
    rules here) is what makes these assertions about the verdict a reader of the
    control plane actually sees, not about the kwargs we happened to pass.
    """
    import importlib.util  # noqa: PLC0415 - lazy; only this helper needs it

    src = (
        Path(__file__).parent.parent
        / "apps/web-server/server/services/job_state_status.py"
    )
    spec = importlib.util.spec_from_file_location("_jss_status_for_test", src)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return str(
        mod.to_lifecycle_state(
            row.get("service_status"), has_verdict=bool(row.get("has_verdict"))
        )
    )


@pytest.fixture
def durable_rows(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Capture what ``_record_terminal`` writes to the durable job-state store.

    The real ``_record_terminal`` runs — only the sibling app's store module is
    stubbed — so the classification under test (#1243: a lost workspace must not
    leave a ``done`` row) is the module's own, not the test's.
    """
    import sys  # noqa: PLC0415 - lazy; fixture-local
    import types  # noqa: PLC0415 - lazy; fixture-local

    rows: list[dict[str, Any]] = []

    async def _record_terminal(job_id: str, **kw: Any) -> None:
        rows.append({"job_id": job_id, **kw})

    server = types.ModuleType("server")
    services = types.ModuleType("server.services")
    jss = types.ModuleType("server.services.job_state_store")
    jss.record_terminal = _record_terminal  # type: ignore[attr-defined]
    services.job_state_store = jss  # type: ignore[attr-defined]
    server.services = services  # type: ignore[attr-defined]
    for name, mod in (
        ("server", server),
        ("server.services", services),
        ("server.services.job_state_store", jss),
    ):
        monkeypatch.setitem(sys.modules, name, mod)
    return rows


def _run_pipeline_main(
    monkeypatch: pytest.MonkeyPatch,
    *,
    uri: str,
    workdir: Path,
    on_run: Any,
    record: Any = None,
) -> int:
    """Drive ``verify_pipeline.main`` on the packed path with the stages stubbed."""
    from agents import verify_pipeline as vp

    async def _fake_pipeline(spec_dir: Path, _project_dir: Path, **_kw: Any) -> Any:
        on_run(spec_dir)
        return True, "triaged"

    async def _fake_record(*_a: Any, **_kw: Any) -> None:
        return None

    monkeypatch.setattr(vp, "run_verify_pipeline", _fake_pipeline)
    if record is None:
        monkeypatch.setattr(vp, "_record_terminal", _fake_record)
    monkeypatch.setattr(vp, "repair_linked_worktree", lambda _p: None)
    monkeypatch.setenv(vw.ENV_WORKSPACE_URI, uri)
    monkeypatch.setenv(vw.ENV_WORKSPACE_ROOT, str(workdir))
    spec = workdir / "workspaces" / "proj-1" / "specs" / "spec-7"
    argv = ["--spec", str(spec), "--project", str(spec / ".worktree")]
    argv += ["--job-id", "job-1", "--correlation-key", "42"]
    return vp.main(argv)


def test_pipeline_main_unpacks_then_pushes_the_evidence_back(
    monkeypatch: pytest.MonkeyPatch, fake_s3: _FakeS3, data_root: Path, tmp_path: Path
) -> None:
    """The whole #1159 contract through the real entrypoint: the Job starts from an
    empty dir, and what it wrote there is in the object store once it exits."""
    uri = _pack(data_root)
    workdir = tmp_path / "emptydir"

    def _produce_evidence(spec_dir: Path) -> None:
        # The stages only ever see what the unpack put on disk.
        assert (spec_dir / ".worktree" / "test_thing.py").is_file()
        _write(spec_dir / "findings" / "evidence" / "shot.png", "PNG")
        _write(spec_dir / "status.json", '{"status": "triaged"}')

    rc = _run_pipeline_main(
        monkeypatch, uri=uri, workdir=workdir, on_run=_produce_evidence
    )
    assert rc == 0

    # The pod is gone; only the object survives.
    restored = tmp_path / "after"
    vw.restore_workspace(uri=uri, root=str(restored))
    out = restored / "workspaces" / "proj-1" / "specs" / "spec-7"
    assert out.joinpath("findings", "evidence", "shot.png").read_text() == "PNG"
    assert out.joinpath("status.json").read_text() == '{"status": "triaged"}'


def test_pipeline_main_fails_the_job_when_evidence_cannot_be_pushed_back(
    monkeypatch: pytest.MonkeyPatch, fake_s3: _FakeS3, data_root: Path, tmp_path: Path
) -> None:
    """A green Job with no evidence is the outcome this issue exists to prevent."""
    uri = _pack(data_root)

    def _boom(**_kw: Any) -> str:
        raise RuntimeError("minio down")

    monkeypatch.setattr(vw, "push_back_workspace", _boom)
    rc = _run_pipeline_main(
        monkeypatch, uri=uri, workdir=tmp_path / "emptydir", on_run=lambda _s: None
    )
    assert rc == 1


# -- #1243: the DURABLE verdict, not just the pod's exit code ------------------


def test_failed_push_back_makes_the_durable_verdict_stuck(
    monkeypatch: pytest.MonkeyPatch,
    fake_s3: _FakeS3,
    data_root: Path,
    tmp_path: Path,
    durable_rows: list[dict[str, Any]],
) -> None:
    """The row the control plane reads must NOT say done.

    Exiting non-zero was already true before #1243 and was not enough:
    ``reconcile_and_reap_once`` short-circuits on "a terminal row the Job wrote
    wins" and never probes the Job, so a ``done`` row written before the
    push-back is the final word. The verdict itself has to carry the loss.
    """
    uri = _pack(data_root)

    def _boom(**_kw: Any) -> str:
        raise RuntimeError("minio down")

    monkeypatch.setattr(vw, "push_back_workspace", _boom)
    rc = _run_pipeline_main(
        monkeypatch,
        uri=uri,
        workdir=tmp_path / "emptydir",
        on_run=lambda _s: None,
        record=True,
    )

    assert rc == 1
    assert len(durable_rows) == 1, durable_rows
    row = durable_rows[0]
    assert row["has_verdict"] is False
    assert "minio down" in str(row["error"])
    assert _lifecycle_of(row) == "stuck"


def test_successful_push_back_still_records_the_real_verdict(
    monkeypatch: pytest.MonkeyPatch,
    fake_s3: _FakeS3,
    data_root: Path,
    tmp_path: Path,
    durable_rows: list[dict[str, Any]],
) -> None:
    """The mutation's control: with the push-back working the row stays done."""
    rc = _run_pipeline_main(
        monkeypatch,
        uri=_pack(data_root),
        workdir=tmp_path / "emptydir",
        on_run=lambda _s: None,
        record=True,
    )

    assert rc == 0
    assert durable_rows[0]["has_verdict"] is True
    assert durable_rows[0]["error"] is None
    assert _lifecycle_of(durable_rows[0]) == "done"


def test_failed_restore_still_writes_a_terminal_row(
    monkeypatch: pytest.MonkeyPatch,
    fake_s3: _FakeS3,
    tmp_path: Path,
    durable_rows: list[dict[str, Any]],
) -> None:
    """A restore that raises used to leave NO row at all — the pod just died.

    The row must also not be an active state: an empty status maps to ``queued``,
    which would keep holding an RFC-0016 admission slot forever.
    """
    from agents import verify_pipeline as vp

    def _boom(**_kw: Any) -> Path:
        raise RuntimeError("store unreachable")

    monkeypatch.setattr(vw, "restore_workspace", _boom)
    monkeypatch.setattr(vp, "repair_linked_worktree", lambda _p: None)
    monkeypatch.setenv(vw.ENV_WORKSPACE_URI, "s3://b/k")
    monkeypatch.setenv(vw.ENV_WORKSPACE_ROOT, str(tmp_path / "emptydir"))

    rc = vp.main(
        [
            "--spec",
            str(tmp_path / "spec"),
            "--project",
            str(tmp_path / "spec" / ".worktree"),
            "--job-id",
            "job-1",
        ]
    )

    assert rc == 1
    assert len(durable_rows) == 1, durable_rows
    row = durable_rows[0]
    assert "store unreachable" in str(row["error"])
    assert _lifecycle_of(row) not in ("queued", "running", "done")


# ─── #1160: the control plane restores what a packed Job pushed back ──────────
#
# Until this, nothing on the control plane read the pushed-back object, so every
# PVC reader (portal, handback, liveness, lane_progress) saw the spec as it was
# BEFORE dispatch. Three traps shape the restore: dispatch and push-back share one
# object key (a Job that died leaves the pre-dispatch archive), the archive carries
# the worktree and the shared base clone, and the worktree sits INSIDE the spec
# dir. Every test asserts on files, not on calls.

_SPEC_REL = Path("workspaces/proj-1/specs/spec-7")


def _job_runs(
    tmp_path: Path, uri: str, *, push_back: bool = True, job_id: str = "job-1"
) -> None:
    """Simulate the verify Job: unpack, produce evidence, mark, push back."""
    workdir = tmp_path / "job-emptydir"
    vw.restore_workspace(uri=uri, root=str(workdir))
    spec = workdir / _SPEC_REL
    _write(spec / "status.json", '{"status": "triaged"}\n')
    _write(spec / "findings" / "verdicts.json", '[{"ac": 1, "pass": true}]')
    # The Job also touches the worktree and the base clone — neither may come back.
    _write(spec / ".worktree" / "test_thing.py", "JOB-EDITED\n")
    _write(spec / ".worktree" / ".git", "gitdir: /job/private/path\n")
    _write(workdir / "workspaces" / "demo-repo" / "src" / "app.py", "JOB-EDITED\n")
    if push_back:
        vw.mark_pushed_back(spec, job_id)
        vw.push_back_workspace(root=workdir, job_id="job-1", correlation_key=42)


def _restore(data_root: Path, uri: str, **kw: Any) -> bool | None:
    spec = data_root / _SPEC_REL
    return vw.restore_spec_from_workspace(
        spec_dir=spec,
        project_dir=spec / ".worktree",
        job_id=kw.pop("job_id", "job-1"),
        uri=uri,
        data_root=str(data_root),
        **kw,
    )


def _sentinel(data_root: Path) -> dict[str, Any] | None:
    import json  # noqa: PLC0415 - test-local

    p = data_root / _SPEC_REL / vw.RESTORED_SENTINEL
    return json.loads(p.read_text()) if p.is_file() else None


def test_restore_brings_back_the_jobs_spec_tree(
    fake_s3: _FakeS3, data_root: Path, tmp_path: Path
) -> None:
    uri = _pack(data_root)
    _job_runs(tmp_path, uri)

    assert _restore(data_root, uri) is True

    spec = data_root / _SPEC_REL
    assert spec.joinpath("status.json").read_text() == '{"status": "triaged"}\n'
    assert spec.joinpath("findings", "verdicts.json").read_text() == (
        '[{"ac": 1, "pass": true}]'
    )
    assert (_sentinel(data_root) or {}).get("restored") is True
    # The marker is proof about ONE archive; it must not land on the PVC, where
    # the next dispatch would pack it and vouch for a Job that never pushed back.
    assert not spec.joinpath(vw.PUSHED_BACK_MARKER).exists()


def test_restore_never_touches_the_worktree_or_the_base_clone(
    fake_s3: _FakeS3, data_root: Path, tmp_path: Path
) -> None:
    """The worktree sits INSIDE the spec dir and the base clone is shared by every
    spec; restoring one Job's copy of either would corrupt them."""
    spec = data_root / _SPEC_REL
    before = {
        p: p.read_bytes()
        for p in (
            spec / ".worktree" / "test_thing.py",
            spec / ".worktree" / ".git",
            data_root / "workspaces" / "demo-repo" / "src" / "app.py",
        )
    }
    uri = _pack(data_root)
    _job_runs(tmp_path, uri)

    assert _restore(data_root, uri) is True

    assert {p: p.read_bytes() for p in before} == before


def test_no_push_back_restores_nothing(
    fake_s3: _FakeS3, data_root: Path, tmp_path: Path
) -> None:
    """The Job died before pushing back, so the object is still the PRE-dispatch
    pack. Restoring it would overwrite the reaper's `stuck` with `planned`."""
    uri = _pack(data_root)
    _job_runs(tmp_path, uri, push_back=False)
    spec = data_root / _SPEC_REL
    _write(spec / "status.json", '{"status": "stuck"}\n')  # the reaper's write

    assert _restore(data_root, uri) is False

    assert spec.joinpath("status.json").read_text() == '{"status": "stuck"}\n'
    s = _sentinel(data_root) or {}
    assert s.get("restored") is False
    assert "no push-back" in s.get("reason", "")


def test_a_marker_from_another_job_is_not_trusted(
    fake_s3: _FakeS3, data_root: Path, tmp_path: Path
) -> None:
    """A stale marker (an earlier run's, packed along with the spec dir) must not
    vouch for this Job's archive."""
    uri = _pack(data_root)
    _job_runs(tmp_path, uri, job_id="some-earlier-job")
    spec = data_root / _SPEC_REL
    _write(spec / "status.json", '{"status": "stuck"}\n')

    assert _restore(data_root, uri) is False
    assert spec.joinpath("status.json").read_text() == '{"status": "stuck"}\n'


def test_a_fetch_failure_retries_then_gives_up_visibly(
    fake_s3: _FakeS3, data_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Never swallowed: no sentinel while retrying, so the next tick tries again;
    a permanently broken object ends visible, not retried forever."""
    monkeypatch.setenv(vw.ENV_RESTORE_MAX_ATTEMPTS, "3")
    missing = f"s3://{_BUCKET}/tfactory/42/job-1/workspace/workspace.tar.gz"
    for _ in range(2):
        assert _restore(data_root, missing) is None
        assert _sentinel(data_root) is None
    assert _restore(data_root, missing) is False
    s = _sentinel(data_root) or {}
    assert s.get("restored") is False
    assert s.get("error")


def test_restore_is_idempotent(
    fake_s3: _FakeS3, data_root: Path, tmp_path: Path
) -> None:
    uri = _pack(data_root)
    _job_runs(tmp_path, uri)
    assert _restore(data_root, uri) is True
    spec = data_root / _SPEC_REL
    _write(spec / "status.json", '{"status": "triaged", "later": true}\n')
    # Already restored: a second pass must not clobber later writes.
    assert _restore(data_root, uri) is True
    assert "later" in spec.joinpath("status.json").read_text()


def test_a_rerun_is_not_blocked_by_the_previous_jobs_sentinel(
    fake_s3: _FakeS3, data_root: Path, tmp_path: Path
) -> None:
    """The sentinel lives in the spec dir, which outlives the Job. A rerun of the
    same spec is a new Job; an earlier job's sentinel must not skip it."""
    import json  # noqa: PLC0415 - test-local

    spec = data_root / _SPEC_REL
    (spec / vw.RESTORED_SENTINEL).write_text(
        json.dumps({"restored": True, "job_id": "job-0"})
    )
    uri = _pack(data_root)
    _job_runs(tmp_path, uri)

    assert _restore(data_root, uri) is True
    assert spec.joinpath("status.json").read_text() == '{"status": "triaged"}\n'
    assert (_sentinel(data_root) or {}).get("job_id") == "job-1"


def test_the_pipeline_marks_before_it_pushes_back(
    monkeypatch: pytest.MonkeyPatch, fake_s3: _FakeS3, data_root: Path, tmp_path: Path
) -> None:
    """Through the real Job entrypoint: the pushed-back object carries the marker
    for this job, so the control plane will trust it."""
    import json  # noqa: PLC0415 - test-local

    uri = _pack(data_root)
    rc = _run_pipeline_main(
        monkeypatch, uri=uri, workdir=tmp_path / "emptydir", on_run=lambda _s: None
    )
    assert rc == 0
    after = tmp_path / "after"
    vw.restore_workspace(uri=uri, root=str(after))
    marker = after / _SPEC_REL / vw.PUSHED_BACK_MARKER
    assert json.loads(marker.read_text())["job_id"] == "job-1"


# ─── the sweep: which specs get restored, and when ───────────────────────────


class _Rows:
    """Just the ``get`` the sweep needs from the durable job-state store."""

    def __init__(self, rows: dict[str, dict[str, Any]], fail: bool = False) -> None:
        self.rows = rows
        self.fail = fail

    async def get(self, job_id: str) -> dict[str, Any] | None:
        if self.fail:
            raise RuntimeError("store down")
        return self.rows.get(job_id)


def _worker_ref(data_root: Path, uri: str | None) -> None:
    import json  # noqa: PLC0415 - test-local

    from agents.verify_dispatch import SPEC_WORKER_REF_FILE  # noqa: PLC0415

    spec = data_root / _SPEC_REL
    ref: dict[str, Any] = {"kind": "k8s-job", "job_id": "job-1", "spec_dir": str(spec)}
    if uri is not None:
        ref |= {"workspace_uri": uri, "project_dir": str(spec / ".worktree")}
    (spec / SPEC_WORKER_REF_FILE).write_text(json.dumps(ref))


def _sweep(data_root: Path, rows: _Rows) -> int:
    import asyncio  # noqa: PLC0415 - test-local

    from agents.verify_dispatch import restore_packed_workspaces_once  # noqa: PLC0415

    return asyncio.run(restore_packed_workspaces_once(rows, str(data_root)))


def test_sweep_restores_a_terminal_packed_spec(
    fake_s3: _FakeS3, data_root: Path, tmp_path: Path
) -> None:
    uri = _pack(data_root)
    _job_runs(tmp_path, uri)
    _worker_ref(data_root, uri)

    assert _sweep(data_root, _Rows({"job-1": {"lifecycle_state": "done"}})) == 1
    status = (data_root / _SPEC_REL / "status.json").read_text()
    assert status == '{"status": "triaged"}\n'


def test_sweep_leaves_a_running_job_alone(
    fake_s3: _FakeS3, data_root: Path, tmp_path: Path
) -> None:
    uri = _pack(data_root)
    _job_runs(tmp_path, uri)
    _worker_ref(data_root, uri)

    assert _sweep(data_root, _Rows({"job-1": {"lifecycle_state": "running"}})) == 0
    assert _sentinel(data_root) is None
    status = (data_root / _SPEC_REL / "status.json").read_text()
    assert status == '{"status": "planned"}\n'


def test_sweep_ignores_a_co_mounted_dispatch(fake_s3: _FakeS3, data_root: Path) -> None:
    """No workspace_uri on the worker ref = not packed = never fetched."""
    _worker_ref(data_root, None)
    rows = _Rows({"job-1": {"lifecycle_state": "done"}})
    assert _sweep(data_root, rows) == 0
    assert _sentinel(data_root) is None
    assert fake_s3.objects == {}


def test_sweep_never_raises(fake_s3: _FakeS3, data_root: Path) -> None:
    _worker_ref(data_root, f"s3://{_BUCKET}/x")
    assert _sweep(data_root, _Rows({}, fail=True)) == 0
