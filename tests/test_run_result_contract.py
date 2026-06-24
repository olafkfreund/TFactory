"""Guard the shared run-result contract (agents/run_result.py, #426).

The execution-engine unification rests on a single structural result type that
every backend must satisfy. These tests pin that contract:

  - ``DockerRunResult`` (the Docker per-lane path) conforms structurally.
  - The historical per-module ``_RunResultLike`` aliases all resolve to the one
    shared ``RunResultLike`` (no more triplicated copies).
  - The Nix-Job ``JobRunResult`` also conforms (via alias properties), so both
    execution engines return one structural shape.
"""

from __future__ import annotations

from agents.run_result import RunResultLike


def test_docker_run_result_satisfies_contract():
    from tools.runners.docker_runner import DockerRunResult

    res = DockerRunResult(returncode=0, stdout="out", stderr="err")
    assert isinstance(res, RunResultLike)
    assert res.returncode == 0 and res.stdout == "out" and res.stderr == "err"


def test_module_aliases_are_the_single_shared_protocol():
    # The light agent modules alias the shared Protocol under the old name so
    # their existing annotations keep working — assert they are the SAME object.
    import agents.mutate_probe as mutate_probe
    import agents.stability_runner as stability_runner

    assert stability_runner._RunResultLike is RunResultLike
    assert mutate_probe._RunResultLike is RunResultLike


def test_non_conforming_object_is_rejected():
    class _Bare:
        returncode = 0  # missing stdout / stderr

    assert not isinstance(_Bare(), RunResultLike)


def test_job_run_result_conforms_via_alias_properties():
    # The Nix-Job result now exposes the structural surface too: returncode,
    # stdout and stderr alias exit_code / output / "" so both engines match.
    from tools.runners.kube_sandbox import JobRunResult

    job = JobRunResult(ok=False, exit_code=2, output="boom")
    assert isinstance(job, RunResultLike)
    assert job.returncode == 2
    assert job.stdout == "boom"
    assert job.stderr == ""
