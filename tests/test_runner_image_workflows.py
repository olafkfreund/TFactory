"""Every runner image the cluster pulls must be built by CI (#886).

`tfactory-runner-portal-ui` had no build workflow at all. The lane runs it as a
k8s Job from `:latest`, `:latest` was a hand-run `docker build` from five weeks
earlier, and so #875/#876/#877 were merged, green, and never executed anywhere:
the in-cluster harness kept joining the `tfactory` Service and 502ing the portal
it was testing. Nothing reported drift, because there was nothing to drift
*from* — no workflow, no run, no failure.

Patching that one image would leave the next one to be discovered the same way.
These tests assert the invariant instead: every `docker/tfactory-runner-*/`
directory is built AND pushed by some workflow, and each workflow's `paths`
filter can actually reach the files that go into its image.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parents[1]
_DOCKER = _ROOT / "docker"
_WORKFLOWS = _ROOT / ".github" / "workflows"

_LITERAL = re.compile(r"docker/tfactory-runner-([a-z0-9][a-z0-9-]*)/Dockerfile")
_MATRIX = "docker/tfactory-runner-${{ matrix.runner }}/Dockerfile"


def _runner_dirs() -> set[str]:
    """The runner image directories that exist on disk."""
    return {
        d.name.removeprefix("tfactory-runner-")
        for d in _DOCKER.iterdir()
        if d.is_dir() and d.name.startswith("tfactory-runner-")
    }


def _glob_to_re(pattern: str) -> re.Pattern[str]:
    """GitHub Actions path glob -> regex. `**` spans separators, `*` does not."""
    out: list[str] = []
    i = 0
    while i < len(pattern):
        if pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("^" + "".join(out) + "$")


def _triggers_on(patterns: list[str], path: str) -> bool:
    """Would this `paths:` filter fire for `path`? Last match wins; `!` negates."""
    hit = False
    for pattern in patterns:
        negated = pattern.startswith("!")
        if _glob_to_re(pattern.removeprefix("!")).match(path):
            hit = not negated
    return hit


def _builds(text: str, doc: dict) -> set[str]:
    """Which runner images does this workflow build?"""
    built = set(_LITERAL.findall(text))
    if _MATRIX in text:
        for job in (doc.get("jobs") or {}).values():
            matrix = ((job.get("strategy") or {}).get("matrix") or {}).get("runner")
            built.update(matrix or [])
    return built


def _workflows() -> dict[str, tuple[str, dict]]:
    out = {}
    for wf in sorted(_WORKFLOWS.glob("*.yml")):
        text = wf.read_text()
        # `on:` parses to the boolean True (YAML 1.1); harmless, we read it back
        # by that key where needed.
        out[wf.name] = (text, yaml.safe_load(text))
    return out


_PUSH_ENABLED = re.compile(r"^\s*push:\s*(true|\"?\$\{\{)", re.MULTILINE)
# The login-action input, matched as a pattern rather than a substring: a bare
# `"ghcr.io" in text` reads to CodeQL as incomplete URL sanitization
# (py/incomplete-url-substring-sanitization), and it is also the weaker
# assertion -- this one says the workflow authenticates to *that* registry.
_GHCR_LOGIN = re.compile(r"registry:\s*ghcr\.io")


def test_every_runner_image_directory_is_built_and_pushed_by_ci():
    """The #886 invariant. Add a runner image without a workflow and this fails."""
    workflows = _workflows()
    owners: dict[str, list[str]] = {name: [] for name in _runner_dirs()}
    for wf_name, (text, doc) in workflows.items():
        for runner in _builds(text, doc):
            if runner in owners:
                owners[runner].append(wf_name)

    unbuilt = sorted(name for name, wfs in owners.items() if not wfs)
    assert not unbuilt, (
        f"docker/tfactory-runner-{{{','.join(unbuilt)}}}/ has no build workflow. "
        "The cluster pulls these by tag; with nothing building them, a merged "
        "change to their contents never runs anywhere (#886)."
    )

    # Built is not shipped. Each owning workflow must actually publish to GHCR,
    # or the registry tag the cluster pulls stays whatever someone last built by
    # hand -- which is the whole of #886.
    for runner, wfs in owners.items():
        for wf_name in wfs:
            text = workflows[wf_name][0]
            assert _GHCR_LOGIN.search(text) and _PUSH_ENABLED.search(text), (
                f"{wf_name} builds tfactory-runner-{runner} but never pushes it "
                "to GHCR, so the tag the cluster pulls is unaffected."
            )


def _publishers() -> dict[str, str]:
    """Workflow name -> text, for every workflow that pushes a runner image."""
    dirs = _runner_dirs()
    return {
        wf_name: text
        for wf_name, (text, doc) in _workflows().items()
        if (_builds(text, doc) & dirs)
        and _GHCR_LOGIN.search(text)
        and _PUSH_ENABLED.search(text)
    }


def _pinned_identity(wf_name: str) -> str:
    """The anchored cosign subject a workflow's own signatures carry.

    GitHub mints the signing certificate with
    `https://github.com/<owner>/<repo>/.github/workflows/<file>@<ref>` as the
    SAN, so the identity is a property of the workflow file, not a choice.
    """
    escaped = wf_name.replace(".", "\\.")
    return (
        r"^https://github\.com/${{ github.repository }}"
        rf"/\.github/workflows/{escaped}"
        r"@refs/heads/main$"
    )


def test_every_published_runner_image_is_signed_with_a_pinned_identity():
    """Factory#524: the sandbox images were the unsigned half of the fleet.

    `verify-factory-image-signatures` pins each service image to the exact
    publishing workflow on refs/heads/main. The runner images — the sandbox in
    which *generated* code is built and executed — were signed by nothing, so
    the Kyverno rule that will cover them has nothing to verify against.

    Three assertions, each of which has failed silently somewhere before:
      - `id-token: write`, without which keyless signing cannot mint a cert;
      - a `cosign sign` on the published digest;
      - a self-test pinned to the SAME anchored identity the gate uses. A
        prefix-only self-test passes on signatures the gate rejects, which is
        exactly the hole Factory#522 found in the policy.
    """
    publishers = _publishers()
    assert publishers, "premise changed: no workflow publishes a runner image"

    for wf_name, text in sorted(publishers.items()):
        assert re.search(r"^\s*id-token:\s*write", text, re.MULTILINE), (
            f"{wf_name} publishes a runner image but does not request "
            "`id-token: write`; cosign keyless signing cannot mint a "
            "certificate without it."
        )
        assert "cosign sign --yes" in text, (
            f"{wf_name} publishes a runner image without signing it. The "
            "admission policy cannot verify what was never signed (Factory#524)."
        )
        assert _pinned_identity(wf_name) in text, (
            f"{wf_name}'s cosign self-test must assert the anchored identity\n"
            f"  {_pinned_identity(wf_name)}\n"
            "and nothing looser. A self-test weaker than the admission rule it "
            "models reports success on a signature the gate would deny "
            "(Factory#522)."
        )


_SOURCE_LABEL = (
    "org.opencontainers.image.source=https://github.com/${{ github.repository }}"
)


def test_every_published_runner_image_is_labelled_with_its_source_repo():
    """#952: GHCR links a package to a repo via `org.opencontainers.image.source`.

    Without it the package is linked to nothing, so it inherits neither the
    repository's visibility nor its package permissions, and its GHCR page
    offers no path back to the code and workflow that built it.
    `tfactory-runner-nix` and `tfactory-runner-portal-ui` were the two of eleven
    whose Dockerfiles omitted it, and they were the two that diverged from their
    nine siblings.

    Asserted on the WORKFLOW rather than on the Dockerfiles, which is the point.
    Nine authors remembered the LABEL line and two did not, and nothing --- not
    CI, not the workflow log, not the image --- made the omission visible; it
    showed up only in package metadata. A `labels:` on the push step cannot be
    forgotten by the author of the next runner image.
    """
    publishers = _publishers()
    assert publishers, "premise changed: no workflow publishes a runner image"

    for wf_name, text in sorted(publishers.items()):
        assert _SOURCE_LABEL in text, (
            f"{wf_name} publishes a runner image without setting\n"
            f"  labels: {_SOURCE_LABEL}\n"
            "on its push step. The GHCR package will be linked to no "
            "repository, and so will inherit neither this repo's visibility "
            "nor its package permissions (#952, Factory#563)."
        )


# A job whose `if:` requires the push event cannot run on a pull_request. This
# is the idiom all three publishing workflows use; anything else is treated as
# PR-reachable, which is the conservative direction for a permission guard.
_PUSH_ONLY_IF = re.compile(r"""github\.event_name\s*==\s*['"]push['"]""")


def _grants_id_token(perms: object) -> bool:
    """Does this `permissions:` block hand out `id-token: write`?"""
    if perms is None:
        # No block at all -> the repo default GITHUB_TOKEN permissions, which
        # never include id-token. Keyless signing is opt-in by construction.
        return False
    if isinstance(perms, str):
        return perms == "write-all"
    return isinstance(perms, dict) and perms.get("id-token") == "write"


def test_pull_request_runs_are_not_granted_id_token_write():
    """#948: the OIDC capability must not exist on the PR path.

    `permissions:` takes no expression, so a workflow that triggers on both
    `push` and `pull_request` with a single job grants that job's capabilities
    to BOTH. All three runner-image workflows did exactly that: every `cosign`
    step was `if: github.event_name == 'push'`, but `id-token: write` was not,
    so a pull_request build -- which executes PR-controlled Dockerfile
    instructions -- could mint a GitHub OIDC token for this repository.

    It was not exploitable through the signing control itself: a PR-minted
    certificate carries `...@refs/pull/N/merge`, and both the self-tests above
    and the live `verify-tfactory-runner-signature` rule anchor
    `...@refs/heads/main$`. The residual was every OTHER consumer that trusts
    `repo:olafkfreund/TFactory` without pinning the ref. Defence in depth is
    not worth relying on the absence of such a consumer.

    A step-level `if:` cannot fix this -- only a job boundary can, because
    permissions are granted per job. So this asserts the boundary, not the
    guards on the steps.
    """
    checked = 0
    for wf_name, (_text, doc) in sorted(_workflows().items()):
        on = doc[True] if True in doc else doc.get("on")
        if not isinstance(on, dict) or "pull_request" not in on:
            continue  # push-only workflow (deploy.yml, release.yml): not at risk

        for job_name, job in (doc.get("jobs") or {}).items():
            if _PUSH_ONLY_IF.search(str(job.get("if", ""))):
                continue  # unreachable from a pull_request
            checked += 1
            # A job-level block replaces the workflow-level one outright.
            perms = job.get("permissions", doc.get("permissions"))
            assert not _grants_id_token(perms), (
                f"{wf_name}: job `{job_name}` can run on `pull_request` and is "
                "granted `id-token: write`. `permissions:` takes no expression, "
                "so guarding the cosign STEPS with "
                "`if: github.event_name == 'push'` does not withhold the token "
                "capability from the PR run -- only a push-only job does (#948)."
            )

    assert checked, (
        "premise changed: no pull_request-reachable job found in any workflow"
    )


# cosign is the only thing in this repo that consumes a GitHub OIDC token. If a
# job acquires a legitimate second use (a cloud `configure-*-credentials`, say),
# widen this deliberately -- do not delete the assertion.
_SIGNS = re.compile(r"cosign\s+(sign|attest)")


def test_only_jobs_that_sign_are_granted_id_token_write():
    """#957: `id-token: write` must be declared per job, never inherited.

    `deploy.yml` declared it at workflow level, so `seam-check` -- which checks
    out a public repo and makes outbound HTTP calls -- was handed the OIDC token
    too. That token is the signing identity: `cosign sign` uses it to mint a
    Sigstore certificate whose subject is this repository, and since Factory#522
    a signature is what admits an image to the cluster. So a job that can mint it
    can sign something the admission gate will then trust.

    Sibling of `test_pull_request_runs_are_not_granted_id_token_write`, which
    guards the *event* boundary (no PR-reachable job may hold the capability).
    This one guards the *job* boundary on every trigger, including push-only
    workflows like `deploy.yml` and `release.yml` that the other test skips by
    design.

    Deliberately narrower than "no job inherits a capability no step uses" --
    that is a much harder property and would need a model of what every action
    consumes. This asserts only the capability that is a signing identity.
    """
    checked = 0
    for wf_name, (_text, doc) in sorted(_workflows().items()):
        for job_name, job in (doc.get("jobs") or {}).items():
            # A job-level block replaces the workflow-level one outright.
            perms = job.get("permissions", doc.get("permissions"))
            if not _grants_id_token(perms):
                continue
            checked += 1
            steps = " ".join(
                f"{step.get('run', '')} {step.get('uses', '')}"
                for step in (job.get("steps") or [])
            )
            assert _SIGNS.search(steps), (
                f"{wf_name}: job `{job_name}` is granted `id-token: write` but "
                "runs no `cosign sign`/`attest` step. That capability mints a "
                "GitHub OIDC token, which is the identity images are signed "
                "with and the gate admits on -- declare it on the signing job "
                "only, not at workflow level where every job inherits it "
                "(#957, Factory#522)."
            )

    assert checked, "premise changed: no job requests `id-token: write` at all"


def test_portal_ui_workflow_triggers_on_the_code_baked_into_the_image():
    """The Dockerfile is not the only input — it COPYs portal_testing/.

    A filter on `docker/**` alone is the failure #886 describes wearing a
    different hat: the image would rebuild when its Dockerfile changed and never
    when the harness inside it did.
    """
    text, doc = _workflows()["portal-ui-runner-image.yml"]
    assert (
        "COPY portal_testing/"
        in (_DOCKER / "tfactory-runner-portal-ui" / "Dockerfile").read_text()
    ), "premise changed: the image no longer vendors portal_testing/"

    # `on` is the YAML 1.1 boolean True once parsed.
    on = doc[True] if True in doc else doc["on"]
    for event in ("push", "pull_request"):
        patterns = on[event]["paths"]
        for changed in (
            "portal_testing/dispatch.py",
            "portal_testing/keycloak_login.py",
            "docker/tfactory-runner-portal-ui/Dockerfile",
            ".github/workflows/portal-ui-runner-image.yml",
        ):
            assert _triggers_on(patterns, changed), f"{event}: {changed} must rebuild"

        # ...and only that. A rebuild of a 2GB browser image on every backend
        # commit is how a path filter gets deleted in annoyance.
        for untouched in (
            "apps/backend/agents/evaluator.py",
            "docs/nix-reproducible-testing.md",
            "README.md",
            "docker/tfactory-runner-pytest/Dockerfile",
            "apps/frontend-web/src/App.tsx",
        ):
            assert not _triggers_on(patterns, untouched), (
                f"{event}: {untouched} must NOT rebuild the portal-ui image"
            )


def test_runner_images_matrix_does_not_claim_images_it_cannot_build():
    """A trigger that fires and builds nothing looks like coverage.

    `docker/tfactory-runner-*/**` matches the nix and portal-ui directories, but
    neither is in that workflow's matrix (each needs a shape the matrix cannot
    express). Before the negations, a portal-ui change fired this workflow, built
    nine unrelated images and went green.
    """
    text, doc = _workflows()["runner-images.yml"]
    on = doc[True] if True in doc else doc["on"]
    matrix = set(_builds(text, doc))

    # #948 split build and publish into two jobs, so the runner list now appears
    # in two `strategy.matrix` blocks (a matrix job cannot fan out into another
    # matrix job leg-by-leg). Held equal here: a runner added to `build` alone
    # would build on every PR and never be published or signed, which is #886
    # wearing yet another hat.
    matrices = {
        job_name: ((job.get("strategy") or {}).get("matrix") or {}).get("runner")
        for job_name, job in doc["jobs"].items()
    }
    matrices = {name: m for name, m in matrices.items() if m}
    assert len(matrices) == 2, f"expected build+publish matrices, got {matrices}"
    assert len(set(map(tuple, matrices.values()))) == 1, (
        f"runner-images.yml matrices have drifted apart: {matrices}. Every "
        "runner built must also be published and signed."
    )

    for event in ("push", "pull_request"):
        patterns = on[event]["paths"]
        for runner in _runner_dirs():
            path = f"docker/tfactory-runner-{runner}/Dockerfile"
            fires = _triggers_on(patterns, path)
            assert fires == (runner in matrix), (
                f"{event}: runner-images.yml "
                f"{'fires for' if fires else 'ignores'} {path} but "
                f"{'does not build' if fires else 'builds'} it"
            )


def test_the_job_pin_env_var_is_the_one_ci_bumps():
    """dispatch.py reads PORTAL_UI_IMAGE; the workflow must pin that same name.

    Two halves of one mechanism in two repos. A rename on either side leaves the
    lane silently back on the floating `:latest` it was pinned away from.
    """
    dispatch = (_ROOT / "portal_testing" / "dispatch.py").read_text()
    assert 'os.environ.get("PORTAL_UI_IMAGE"' in dispatch
    text = _workflows()["portal-ui-runner-image.yml"][0]
    assert "name: PORTAL_UI_IMAGE, value:" in text
    # The pin must be an immutable tag, not a floating one.
    assert 'echo "sha=sha-$(git rev-parse --short HEAD)"' in text
