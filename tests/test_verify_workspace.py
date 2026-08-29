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
