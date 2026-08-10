"""#768 — the runner image's warm-up flake must stay in lockstep with the generator.

The warm-up flake (`docker/tfactory-runner-nix/warmup/flake.nix`) is realised at
image build time so every verify Job reuses its closure instead of cold-fetching
the toolchain per test (`S x (3 + mutants)` Jobs, each the identical closure).

That only speeds anything up if the baked store paths are the ones a per-task
flake actually asks for. Store paths are a pure function of the nixpkgs rev and
the package set, so these tests pin the warm-up to what
`nix_provisioner.generate_flake` emits for the common Python case. A drift does
not break correctness — it silently reverts to the slow cold-fetch — which is
exactly the kind of regression a test has to catch because nothing else would.
"""

from __future__ import annotations

import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[2] / "apps" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

_WARMUP = (
    Path(__file__).resolve().parents[2]
    / "docker"
    / "tfactory-runner-nix"
    / "warmup"
    / "flake.nix"
)
_DOCKERFILE = (
    Path(__file__).resolve().parents[2]
    / "docker"
    / "tfactory-runner-nix"
    / "Dockerfile"
)


def test_warmup_pins_the_same_nixpkgs_rev_as_the_generator() -> None:
    from tools.runners.nix_provisioner import DEFAULT_NIXPKGS

    warmup = _WARMUP.read_text()
    assert DEFAULT_NIXPKGS in warmup, (
        "warmup/flake.nix must reference nix_provisioner.DEFAULT_NIXPKGS verbatim, "
        f"or the baked store paths will not match what a Job asks for.\n"
        f"expected: {DEFAULT_NIXPKGS}"
    )


def test_warmup_bakes_the_generators_common_python_packages() -> None:
    """Every package the common+requirements case realises must be pre-baked.

    The generator emits pytest + pytest-cov always, and pip whenever the checkout
    declares requirements (#764) — the exact case a real app hits. All three must
    be in the warm-up set or that Job still cold-fetches the missing one's closure.
    """
    warmup = _WARMUP.read_text()
    for pkg in ("pytest", "pytest-cov", "pip"):
        assert f'p."{pkg}"' in warmup, f"warm-up flake is missing {pkg}"


def test_warmup_matches_the_generated_flakes_package_expression() -> None:
    """The withPackages line must be byte-identical to what a requirements repo gets.

    A different set (even reordered) is a different env derivation, so the baked
    wrapper would not match. Build the generator's flake for a checkout that has
    requirements.txt and assert the warm-up carries the identical packages line.
    """
    import tempfile

    from tools.runners.nix_provisioner import generate_flake

    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        (root / "apps" / "web").mkdir(parents=True)
        (root / "apps" / "web" / "requirements.txt").write_text("fastapi\n")
        generated = generate_flake(
            {"language": "python", "verify_commands": ["pytest -q"]},
            project_dir=root,
        )

    def _pkg_line(text: str) -> str:
        # The code line, not a comment that merely mentions withPackages.
        line = next(ln for ln in text.splitlines() if "pkgs.python" in ln)
        return line.strip()

    assert _pkg_line(_WARMUP.read_text()) == _pkg_line(generated), (
        "warm-up withPackages set drifted from the generator's requirements-case set"
    )


def test_dockerfile_realises_the_warmup() -> None:
    df = _DOCKERFILE.read_text()
    assert "COPY warmup /warmup" in df, "Dockerfile must vendor the warm-up flake"
    assert "nix develop" in df and "/warmup" in df, (
        "Dockerfile must realise the warm-up closure into the image store"
    )
