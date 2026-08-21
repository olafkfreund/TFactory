"""Live differential/equivalence lane execution (RFC-0010 gap closure).

:mod:`agents.equivalence_runner` owns the *pure* comparison + honest reporting,
with ``capture_oracle``/``run_candidate`` injected. This module provides the real
execution: it generates the legacy-source **oracle harness**, runs both the
oracle and the new candidate over the same input vectors (each inside the runner
the verify path already uses), and turns the result into ``equivalence`` verdicts
+ a content-hashed golden corpus.

The bright line (RFC-0010 §2): the planner never executes anything. The legacy
source is run here, inside the sandbox the verify path provides (DockerRunner
``--network=none --read-only`` with ``ci_parity_env``).

**Harness protocol (language-neutral).** A harness reads a JSON array of input
vectors on stdin — ``[{"id","module","function","args","kwargs","critical"}]`` —
and writes a JSON array of results on stdout —
``[{"id","module","output"}]`` or ``[{"id","error":"<ErrorClass>"}]``. The
Python oracle harness is generated here (we own the common source language); the
target impl supplies a ``parity_harness`` conforming to the same protocol
(scaffolded by AIFactory rewrite mode).
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from agents.equivalence_runner import ParityReport, compare_corpus

_log = logging.getLogger(__name__)

if TYPE_CHECKING:
    from agents.execution_sandbox import ExecutionSandbox

# A runner executes a command in the sandbox and returns an object exposing
# ``.stdout`` (str) and ``.returncode`` (int). Matches the DockerRunner result
# shape the evaluator already uses. ``.returncode`` is REQUIRED, not optional
# (TFactory#959): without a status there is no way to tell a harness that ran
# and found nothing from one that never started, and this lane reads the
# difference as a golden corpus.
RunnerFn = Callable[..., Any]


class HarnessDidNotRunError(RuntimeError):
    """A parity harness produced no results and did not exit cleanly.

    Distinct from an empty corpus, which is a MEASUREMENT ("the harness ran;
    there was nothing to compare"). This is the ABSENCE of one.
    """


def _require_harness_ran(what: str, result: Any, parsed: list[dict[str, Any]]) -> None:
    """Raise unless *what*'s harness actually produced a measurement.

    Same rule as ``scripts/ratchet_helpers.require_tool_ran`` (Factory#590),
    minus a tier this harness does not have. ruff and mypy exit 1 for "ran and
    found something"; the parity protocol reports divergence *in its stdout
    JSON* and still exits 0, so 0 is the only exit code that means "ran".

    *parsed* is this lane's version of that helper's ``measured`` qualifier. A
    target-supplied ``parity_harness`` is third-party code that may exit
    non-zero having still emitted a full corpus (a wrapper propagating some
    other status), and results on stdout prove it ran. Only "no results AND not
    a clean exit" is the failure.

    The case ruff and mypy never face: ``runner_fn`` is injected and duck-typed,
    so ``.returncode`` may be missing altogether. A runner that cannot report a
    status cannot show its harness ran, so that fails closed too — reading the
    silence as clean is the whole defect.
    """
    if parsed:
        return
    rc = getattr(result, "returncode", None)
    if rc == 0:
        return
    detail = (
        f"exited {rc}"
        if rc is not None
        else "reported no exit status (its runner carries no `.returncode`)"
    )
    stderr = (getattr(result, "stderr", "") or "").strip()
    raise HarnessDidNotRunError(
        f"the {what} harness {detail} and produced no results, so it did not run"
        + (f"; stderr: {stderr[:500]}" if stderr else "")
    )


_PY_ORACLE_HARNESS = """\
# AUTO-GENERATED RFC-0010 oracle harness — runs the legacy source over the
# golden-corpus input vectors and emits language-neutral JSON results. Read only.
import importlib, json, sys

# Vectors come from argv[1] (a file in the sandbox scratch) or stdin.
if len(sys.argv) > 1:
    with open(sys.argv[1]) as _f:
        vectors = json.load(_f)
else:
    vectors = json.load(sys.stdin)
results = []
for v in vectors:
    rec = {"id": v["id"]}
    if v.get("module"):
        rec["module"] = v["module"]
    try:
        mod = importlib.import_module(v["module"].replace("/", ".").removesuffix(".py"))
        fn = getattr(mod, v["function"])
        out = fn(*v.get("args", []), **v.get("kwargs", {}))
        rec["output"] = out
    except Exception as exc:  # noqa: BLE001 — record the error CLASS, not the trace
        rec["error"] = type(exc).__name__
    results.append(rec)
print(json.dumps(results, default=repr))
"""


def generate_python_oracle_harness() -> str:
    """The Python oracle harness source (protocol-conformant)."""
    return _PY_ORACLE_HARNESS


def input_vectors(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    """Concrete input vectors from the manifest.

    Prefers explicit ``input_vectors`` (declared by the planner from existing
    tests); falls back to a no-arg call per declared function so the lane can run
    even before concrete vectors are extracted (reported as thin coverage).
    """
    explicit = manifest.get("input_vectors")
    if explicit:
        return list(explicit)
    fallback: list[dict[str, Any]] = []
    for i, fn in enumerate(manifest.get("functions", [])):
        fallback.append(
            {
                "id": f"{fn.get('module', '')}::{fn.get('name', '')}#{i}",
                "module": fn.get("module"),
                "function": fn.get("name"),
                "args": [],
                "kwargs": {},
            }
        )
    return fallback


def _parse_results(stdout: str) -> list[dict[str, Any]]:
    """Parse a harness's JSON-array stdout; tolerate trailing log noise."""
    stdout = (stdout or "").strip()
    if not stdout:
        return []
    # The harness prints the JSON array last; take the final balanced array.
    start = stdout.rfind("[")
    end = stdout.rfind("]")
    if start == -1 or end == -1 or end < start:
        return []
    blob = stdout[start : end + 1]
    try:
        data = json.loads(blob)
    except ValueError:
        # Some log transports (e.g. certain k8s pod-log clients) re-serialise the
        # captured stdout as a Python literal (single quotes). The data is the
        # same; parse it safely as a literal.
        import ast

        try:
            data = ast.literal_eval(blob)
        except (ValueError, SyntaxError):
            return []
    return data if isinstance(data, list) else []


def capture_oracle(
    oracle_root: Path,
    manifest: dict[str, Any],
    runner_fn: RunnerFn,
    *,
    language: str = "python",
) -> list[dict[str, Any]]:
    """Run the legacy source over the input vectors → golden vectors.

    ``runner_fn(harness_path, project_dir, stdin)`` runs the harness in the
    sandbox and returns a result with ``.stdout`` and ``.returncode``. Only
    Python oracles are auto-generated today; other source languages must ship a
    ``parity_harness``.

    Raises :class:`HarnessDidNotRunError` when the harness never ran — an empty
    corpus is only a corpus if something produced it.
    """
    vectors = input_vectors(manifest)
    if language != "python":
        return run_candidate(oracle_root, manifest, runner_fn, what="oracle")
    root = Path(oracle_root)
    root.mkdir(parents=True, exist_ok=True)
    harness = root / ".tfactory_oracle_harness.py"
    harness.write_text(generate_python_oracle_harness(), encoding="utf-8")
    result = runner_fn(harness, root, json.dumps(vectors))
    golden = _parse_results(getattr(result, "stdout", ""))
    _require_harness_ran("oracle", result, golden)
    # The harness emits id/module/output/error; carry the input vector's
    # `critical` flag (declared in the manifest) onto the golden vector so the
    # comparison can fail hard on a critical divergence.
    critical_by_id = {v["id"]: v.get("critical", False) for v in vectors}
    for g in golden:
        if critical_by_id.get(g.get("id")):
            g["critical"] = True
    return golden


def run_candidate(
    candidate_root: Path,
    manifest: dict[str, Any],
    runner_fn: RunnerFn,
    *,
    what: str = "candidate",
) -> list[dict[str, Any]]:
    """Run the target impl's ``parity_harness`` over the same input vectors.

    The target must expose a protocol-conformant harness (AIFactory rewrite mode
    scaffolds one). ``runner_fn`` is the candidate runner (e.g. the Nix env).
    *what* names the side being run, so a non-Python oracle (which is also a
    ``parity_harness``) is diagnosed as the oracle it is.

    Raises :class:`HarnessDidNotRunError` when the harness never ran — a missing
    ``parity_harness`` is exactly the case this catches.
    """
    vectors = input_vectors(manifest)
    result = runner_fn(Path("parity_harness"), candidate_root, json.dumps(vectors))
    parsed = _parse_results(getattr(result, "stdout", ""))
    _require_harness_ran(what, result, parsed)
    return parsed


def _unmeasured(
    reason: str, findings_dir: Path | None, *, verdict: str = "reject"
) -> dict[str, Any]:
    """The lane could not measure. Report THAT, and reject.

    Before TFactory#959 a dead harness surfaced as ``modules X UNPROVEN (no
    corpus coverage)`` — a sentence about the manifest and the candidate, for a
    failure in neither. That is the Factory#430 shape: one message covering both
    "the thing under test is wrong" and "I could not measure it", which sends
    the reader somewhere else entirely. A wrong explanation costs more than none.

    No ``golden_corpus.json`` is written: hashing ``[]`` mints a stable,
    meaningless digest that later runs would compare against.

    ``verdict`` is ``reject`` for a lane that tried and could not measure, and
    ``not_run`` for one that was never enabled — see :func:`record_not_measured`.
    """
    claim = f"Equivalence NOT MEASURED — {reason}. Fix the harness, then re-run."
    if findings_dir is not None:
        findings_dir = Path(findings_dir)
        findings_dir.mkdir(parents=True, exist_ok=True)
        (findings_dir / "equivalence_report.json").write_text(
            json.dumps({"measured": False, "error": reason, "claim": claim}, indent=2),
            encoding="utf-8",
        )
    return {
        "verdicts": [
            {"lane": "equivalence", "verdict": verdict, "reason": reason},
        ],
        "parity_ratio": 0.0,
        "claim": claim,
        "passed": False,
    }


def _corpus_hash(golden: list[dict[str, Any]]) -> str:
    return hashlib.sha256(
        json.dumps(golden, sort_keys=True, default=repr).encode()
    ).hexdigest()


def run_equivalence_lane(
    contract: dict[str, Any],
    *,
    oracle_root: Path,
    candidate_root: Path,
    oracle_runner: RunnerFn,
    candidate_runner: RunnerFn,
    findings_dir: Path | None = None,
) -> dict[str, Any]:
    """Execute the differential lane end to end.

    Captures the oracle (legacy source), runs the candidate, compares, and writes
    ``findings/golden_corpus.json`` + the parity report. Returns
    ``{verdicts, parity_ratio, claim, passed}`` — the verdicts feed val_block at
    VAL-2, where partial parity fails and the gate caps achieved_level.

    A harness that never ran returns a single ``reject`` verdict naming the dead
    harness (see :func:`_unmeasured`), never an empty corpus dressed up as one.
    """
    eq = (contract.get("tfactory") or {}).get("equivalence") or {}
    threshold = eq.get("parity_threshold", 1.0)
    manifest = eq.get("manifest") or eq

    try:
        golden = capture_oracle(oracle_root, manifest, oracle_runner)
        candidate = run_candidate(candidate_root, manifest, candidate_runner)
    except HarnessDidNotRunError as exc:
        # Reject, and say which harness died. Rejecting (not skipping) keeps the
        # lane fail-closed at VAL-2 even when the manifest declares nothing —
        # the path that had no second line of defence at all.
        return _unmeasured(str(exc), findings_dir)

    declared = {
        f.get("module") for f in manifest.get("functions", []) if f.get("module")
    }
    covered = {g.get("module") for g in golden if g.get("module")}
    report: ParityReport = compare_corpus(
        golden, candidate, uncovered_modules=sorted(declared - covered)
    )

    if findings_dir is not None:
        findings_dir = Path(findings_dir)
        findings_dir.mkdir(parents=True, exist_ok=True)
        (findings_dir / "golden_corpus.json").write_text(
            json.dumps({"hash": _corpus_hash(golden), "vectors": golden}, indent=2),
            encoding="utf-8",
        )
        (findings_dir / "equivalence_report.json").write_text(
            json.dumps(
                {
                    "parity_ratio": report.parity_ratio,
                    "matched": report.matched,
                    "total": report.total,
                    "mismatches": report.mismatches,
                    "critical_failed": report.critical_failed,
                    "uncovered_modules": report.uncovered_modules,
                    "claim": report.claim(threshold),
                },
                indent=2,
            ),
            encoding="utf-8",
        )

    return {
        "verdicts": report.verdicts(),
        "parity_ratio": report.parity_ratio,
        "claim": report.claim(threshold),
        "passed": report.passed(threshold),
    }


def merge_verdicts(spec_dir: Path, new_verdicts: list[dict[str, Any]]) -> None:
    """Append equivalence verdicts into the spec's ``findings/verdicts.json``.

    val_block reads this file; the appended ``equivalence``-lane verdicts fold
    into VAL-2 (partial parity → reject → the gate caps achieved_level).
    """
    path = Path(spec_dir) / "findings" / "verdicts.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    doc: dict[str, Any] = {"verdicts": []}
    if path.exists():
        try:
            loaded = json.loads(path.read_text())
            if isinstance(loaded, dict):
                doc = loaded
        except ValueError:
            # Corrupt verdicts.json: prior verdicts in this file are lost,
            # not just skipped — val_block reads this file to compute the
            # achieved VAL level, so silently resetting it would understate
            # what already ran. Log loudly rather than swallow.
            _log.warning(
                "verdicts.json at %s is not valid JSON; resetting it and "
                "losing any previously recorded verdicts",
                path,
            )
    doc.setdefault("verdicts", []).extend(new_verdicts)
    path.write_text(json.dumps(doc, indent=2), encoding="utf-8")


def record_not_measured(
    spec_dir: Path, reason: str, *, verdict: str = "reject"
) -> dict[str, Any]:
    """Record in FINDINGS that a declared equivalence lane did not measure.

    A lane that never ran used to leave one WARNING line in the evaluator log
    and nothing else: no ``equivalence_report.json``, no ``equivalence``
    verdict. ``val_block`` still reported VAL-2 as ``not_run`` so nothing was
    overclaimed — but "the lane was never enabled" and "the lane tried and could
    not run" were indistinguishable in the findings, and the findings are what a
    human reads (#972, Factory#430). A log line is not a record.

    ``verdict`` says which of the two happened:

    - ``reject`` — declared and enabled, tried, could not run (no Docker daemon,
      the Job could not be created, the image is missing). Fail-closed, matching
      the dead-harness path in :func:`_unmeasured`.
    - ``not_run`` — declared but the lane is switched off. An operator choice,
      not a failure: it neither passes nor fails VAL-2, and val_block already
      understands ``not_run`` exactly that way.
    """
    result = _unmeasured(reason, Path(spec_dir) / "findings", verdict=verdict)
    merge_verdicts(spec_dir, result["verdicts"])
    return result


def _docker_oracle_runner(image: str = "tfactory-runner-pytest:latest") -> RunnerFn:
    """Real oracle runner: run the Python harness in a hardened DockerRunner.

    Mounts the source at ``/work:ro`` and a scratch dir (harness + vectors) at
    ``/scratch:rw``, then runs ``python /scratch/harness.py /scratch/vectors.json``
    with ``PYTHONPATH=/work`` under ``--network=none --read-only`` + ci-parity env.
    Imported lazily so unit tests that inject a fake runner never need Docker.
    """
    import shutil as _sh
    import tempfile as _tmp

    from tools.runners.docker_runner import DockerRunner, ci_parity_env

    def _run(harness: Path, root: Path, stdin: str) -> Any:
        scratch = Path(_tmp.mkdtemp(prefix="tf-equiv-"))
        try:
            (scratch / "vectors.json").write_text(stdin, encoding="utf-8")
            (scratch / "harness.py").write_text(
                generate_python_oracle_harness(), encoding="utf-8"
            )
            runner = DockerRunner(image=image, network="none", read_only_rootfs=True)
            env = ci_parity_env()
            env["PYTHONPATH"] = "/work"
            return runner.run(
                repo_path=Path(root),
                scratch_path=scratch,
                command=["python", "/scratch/harness.py", "/scratch/vectors.json"],
                extra_env=env,
            )
        finally:
            _sh.rmtree(scratch, ignore_errors=True)

    return _run


def run_from_spec(
    spec_dir: Path,
    project_dir: Path,
    contract: dict[str, Any],
    *,
    oracle_runner: RunnerFn | None = None,
    candidate_runner: RunnerFn | None = None,
) -> dict[str, Any] | None:
    """Run the equivalence lane for a spec and merge its verdicts.

    Returns None when the contract has no equivalence block. ``oracle_root`` is
    the read-only legacy source AIFactory mounts at ``.aifactory/oracle`` (falls
    back to the project dir); ``candidate_root`` is the built target.
    Runners default to the real DockerRunner-based oracle runner; tests inject
    fakes.
    """
    eq = (contract.get("tfactory") or {}).get("equivalence") or {}
    if not eq:
        return None
    import os

    project_dir = Path(project_dir)
    oracle_root = project_dir / ".aifactory" / "oracle"
    if not oracle_root.exists():
        oracle_root = project_dir
    # Backend: 'docker' (default, local DockerRunner) or 'kube' (in-cluster
    # k8s-Job — the AIFactory/TFactory pods have no container runtime).
    backend = os.getenv("TFACTORY_EQUIVALENCE_BACKEND", "docker").strip().lower()
    if backend == "kube":
        image = os.getenv("TFACTORY_EQUIVALENCE_IMAGE", "tfactory-runner-nix:latest")
        default_runner = _kube_oracle_runner(image)
    else:
        image = os.getenv("TFACTORY_EQUIVALENCE_IMAGE", "tfactory-runner-pytest:latest")
        default_runner = _docker_oracle_runner(image)
    result = run_equivalence_lane(
        contract,
        oracle_root=oracle_root,
        candidate_root=project_dir,
        oracle_runner=oracle_runner or default_runner,
        candidate_runner=candidate_runner or default_runner,
        findings_dir=Path(spec_dir) / "findings",
    )
    merge_verdicts(spec_dir, result["verdicts"])
    return result


def _kube_oracle_runner(
    image: str = "tfactory-runner-nix:latest", namespace: str = "factory"
) -> RunnerFn:
    """Oracle runner via an in-cluster k8s Job (RFC-0005 substrate).

    The pods have no container runtime, so the equivalence lane runs the harness
    as an ephemeral Kubernetes Job. The (small) source tree + harness + vectors
    are embedded base64 in the Job command — for large repos the PVC co-mount
    path (KubeJobSandbox repo_pvc) is preferred. Lazily imports the kube deps so
    unit tests that inject a fake runner never need a cluster.
    """
    import base64
    import io
    import tarfile

    from tools.runners.kube_sandbox import KubeJobSandbox

    def _run(_harness: Path, root: Path, stdin: str) -> Any:
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            tar.add(str(root), arcname=".")
        src = base64.b64encode(buf.getvalue()).decode()
        harness = base64.b64encode(generate_python_oracle_harness().encode()).decode()
        vectors = base64.b64encode(stdin.encode()).decode()
        cmd = (
            "set -e; mkdir -p /tmp/o && "
            f"echo {src} | base64 -d | tar xz -C /tmp/o && "
            f"echo {harness} | base64 -d > /tmp/o/h.py && "
            f"echo {vectors} | base64 -d > /tmp/o/v.json && "
            "cd /tmp/o && PYTHONPATH=/tmp/o python h.py v.json"
        )
        # Consume the live Nix-Job engine through the unified seam (#426). The
        # returned JobRunResult now exposes `.stdout`/`.returncode` directly, so
        # the historical ad-hoc result shim is gone.
        sandbox: ExecutionSandbox = KubeJobSandbox(image=image, namespace=namespace)
        return sandbox.run([cmd], timeout=300)

    return _run
