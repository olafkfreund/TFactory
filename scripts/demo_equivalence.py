#!/usr/bin/env python3
"""Repeatable demo of the RFC-0010 behavioral-equivalence lane.

Builds a tiny "legacy" Python module (the oracle), a faithful rewrite, and a
buggy rewrite, then runs the differential lane and prints the honest parity
verdict for each. Proves the lane end to end without needing the full verify
pipeline.

    # fast, hermetic — runs the harness via subprocess
    apps/backend/.venv/bin/python scripts/demo_equivalence.py

    # run the oracle/candidate INSIDE a hardened DockerRunner container
    apps/backend/.venv/bin/python scripts/demo_equivalence.py --docker
    # (override the image; default is tfactory-runner-pytest:latest)
    TFACTORY_EQUIVALENCE_IMAGE=python:3.12-slim ... --docker

Exit code is non-zero if the faithful rewrite is NOT judged equivalent (so this
doubles as a smoke test).
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent / "apps" / "backend"
sys.path.insert(0, str(_BACKEND))

from agents import equivalence_lane as el  # noqa: E402
from agents.equivalence_runner import compare_corpus  # noqa: E402

_LEGACY = """\
def refund(amount, reason):
    if amount <= 0:
        raise ValueError("amount must be positive")
    return {"refunded": amount, "reason": reason}


def fee(amount):
    return round(amount * 0.029 + 0.30, 2)
"""

_MANIFEST = {
    "functions": [
        {"module": "pay/refund.py", "name": "refund"},
        {"module": "pay/refund.py", "name": "fee"},
    ],
    "input_vectors": [
        {
            "id": "refund-ok",
            "module": "pay/refund.py",
            "function": "refund",
            "args": [100, "x"],
            "critical": True,
        },
        {
            "id": "refund-neg",
            "module": "pay/refund.py",
            "function": "refund",
            "args": [-1, "y"],
        },
        {
            "id": "fee-100",
            "module": "pay/refund.py",
            "function": "fee",
            "args": [100],
            "critical": True,
        },
    ],
}


def _subprocess_runner(harness: Path, root: Path, stdin: str):
    (Path(root) / "vectors.json").write_text(stdin)
    (Path(root) / "h.py").write_text(el.generate_python_oracle_harness())
    r = subprocess.run(
        [sys.executable, "h.py", "vectors.json"],
        cwd=root,
        capture_output=True,
        text=True,
    )
    return type("R", (), {"stdout": r.stdout})()


def _make_module(root: Path, body: str) -> Path:
    (root / "pay").mkdir(parents=True, exist_ok=True)
    (root / "pay" / "__init__.py").write_text("")
    (root / "pay" / "refund.py").write_text(body)
    return root


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--docker", action="store_true", help="run inside a DockerRunner container"
    )
    args = ap.parse_args()

    if args.docker:
        image = os.getenv("TFACTORY_EQUIVALENCE_IMAGE", "tfactory-runner-pytest:latest")
        runner = el._docker_oracle_runner(image)
        print(
            f"[mode] DockerRunner container (--network=none --read-only), image={image}"
        )
    else:
        runner = _subprocess_runner
        print("[mode] subprocess (hermetic)")

    tmp = Path(tempfile.mkdtemp(prefix="demo-equiv-"))
    oracle = _make_module(tmp / "oracle", _LEGACY)
    faithful = _make_module(tmp / "faithful", _LEGACY)
    buggy = _make_module(
        tmp / "buggy", _LEGACY.replace("0.029", "0.039")
    )  # fee() diverges

    print("\n>>> Capturing the legacy oracle over the golden corpus...")
    golden = el.capture_oracle(oracle, _MANIFEST, runner)
    for g in golden:
        print("   ", g)

    print("\n>>> Faithful rewrite vs oracle:")
    good = compare_corpus(golden, el.capture_oracle(faithful, _MANIFEST, runner))
    print("    parity:", f"{good.parity_ratio:.0%}", "| pass:", good.passed(1.0))
    print("    claim:", good.claim(1.0))

    print("\n>>> Buggy rewrite (critical fee() divergence) vs oracle:")
    bad = compare_corpus(golden, el.capture_oracle(buggy, _MANIFEST, runner))
    print("    parity:", f"{bad.parity_ratio:.0%}", "| pass(0.5):", bad.passed(0.5))
    print("    claim:", bad.claim(0.5))

    ok = good.passed(1.0) and not bad.passed(0.5)
    print(
        "\n[result]",
        "OK — lane distinguishes equivalent from divergent" if ok else "UNEXPECTED",
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
