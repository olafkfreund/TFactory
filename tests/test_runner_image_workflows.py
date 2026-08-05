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
