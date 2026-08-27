"""Lane Jobs need headroom for a Nix build that misses the binary cache.

Spec 190 lost two jest verdicts to:

    error: Cannot build '/nix/store/...-python3-3.13.13-env.drv'.
           Reason: builder failed due to signal 9 (Killed).

Signal 9 with the node at 4% of 251Gi is the Job's OWN cgroup limit, not node
exhaustion. A derivation that misses the cache builds locally, inside that
limit, alongside the store copies already in flight.
"""

from __future__ import annotations

import inspect
import re

from tools.runners import kube_sandbox

_MIN_GI = 8


def _default_memory() -> str:
    sig = inspect.signature(kube_sandbox.build_job_manifest)
    return sig.parameters["memory"].default


def test_the_lane_job_has_headroom_for_an_uncached_nix_build():
    gi = int(re.match(r"(\d+)Gi$", _default_memory()).group(1))

    assert gi >= _MIN_GI, (
        f"lane Jobs capped at {gi}Gi; an uncached derivation OOMs at 4Gi (spec 190)"
    )


def test_requests_still_match_limits():
    """Without matching requests the scheduler can oversubscribe one node,
    which is what the reservation is for (RFC-0016)."""
    src = inspect.getsource(kube_sandbox)

    assert '"requests": {"cpu": cpus, "memory": memory}' in src
    assert '"limits": {"cpu": cpus, "memory": memory}' in src
