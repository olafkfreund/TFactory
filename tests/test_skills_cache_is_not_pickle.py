#!/usr/bin/env python3
"""The skills cache must be inert data, and must still round-trip (TFactory#1054).

`skills_service` used to persist its startup index with `pickle.dump` and read
it back with `pickle.load`. `pickle.load` executes arbitrary code during
unpickling, and the version/base-path guards in `_load_cache` run AFTER the
load returns — so they defended nothing. Anyone able to write
`~/.tfactory/skills-cache.pkl` had code execution in the web-server process.

PFactory fixed the identical code in PFactory#533 and the fix never propagated
here; the whole-repo security-sink gate is what makes that class of miss visible.

Two tests, because either one alone is insufficient:

  * the format test would pass on a cache that never loads;
  * the round-trip test would pass on a pickle.

The round-trip is not a formality: the index holds `frozenset` token fields and
`Path` objects, neither of which JSON represents. `pickle` preserved them for
free and JSON does not, so the reconstruction in `_index_from_json` is real
logic and this is the check that fails if it breaks.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "apps" / "web-server"))

from server.services.skills_service import SkillsService  # noqa: E402

FIXTURES_PATH = Path(__file__).parent / "fixtures" / "skills"


def _service(tmp_path: Path) -> SkillsService:
    return SkillsService(skills_base_path=FIXTURES_PATH, cache_path=tmp_path / "skills-cache.json")


def test_the_cache_file_is_json_and_not_a_pickle(tmp_path: Path) -> None:
    """Assert on the ARTEFACT: read the bytes, not the code path that wrote them."""
    service = _service(tmp_path)
    service.build_index()

    cache = tmp_path / "skills-cache.json"
    assert cache.exists(), "the service did not write a cache at all"

    raw = cache.read_bytes()
    # A pickle stream starts with the PROTO opcode (0x80) for protocol >= 2.
    assert raw[:1] != b"\x80", "the cache is a pickle stream"
    payload = json.loads(raw.decode("utf-8"))
    assert payload["version"] == 2
    assert payload["base_path"] == str(FIXTURES_PATH)


def test_the_index_survives_a_save_and_reload(tmp_path: Path) -> None:
    """frozensets and Paths do not survive JSON on their own. Prove they do here."""
    first = _service(tmp_path)
    first.build_index()
    before = first._index  # noqa: SLF001 - this test is about the private index

    second = _service(tmp_path)
    second.build_index()
    after = second._index  # noqa: SLF001

    assert set(after) == set(before), "categories were lost across the cache round-trip"
    for category, entries in before.items():
        reloaded = after[category]
        assert [e.summary for e in reloaded] == [e.summary for e in entries]
        for original, restored in zip(entries, reloaded, strict=True):
            assert isinstance(restored.file_path, Path)
            assert restored.file_path == original.file_path
            assert isinstance(restored.name_tokens, frozenset)
            assert restored.name_tokens == original.name_tokens
            assert restored.description_tokens == original.description_tokens


def test_a_stale_pickle_cache_is_never_opened(tmp_path: Path) -> None:
    """Migration safety: the path changed, so an old .pkl on disk is inert.

    If this fails, the fix is cosmetic — the RCE primitive is still reachable
    for anyone whose home directory already holds the old file.
    """
    hostile = tmp_path / "skills-cache.pkl"
    hostile.write_bytes(b"\x80\x04\x95NOT-A-VALID-PICKLE")

    service = _service(tmp_path)
    service.build_index()

    assert service.list_categories(), "the index did not build"
    assert hostile.read_bytes().startswith(b"\x80\x04\x95"), "the old file was touched"


def test_a_live_reduce_payload_does_not_execute(tmp_path: Path) -> None:
    """The one that would have caught this. Everything above is a proxy for it.

    A real `__reduce__` payload is planted at BOTH cache paths — the old `.pkl`
    and the new `.json` — and the assertion is on the side effect: the marker
    file must not exist afterwards. That is the artefact (rule 4.10). Asserting
    "the code says json.load" is asserting on the process; this asserts that the
    primitive is gone.

    `Path.write_text` rather than anything from `os` or `subprocess`: it proves
    arbitrary-callable execution just as well and cannot do damage if the test
    itself is ever run somewhere unexpected.

    Against the pre-fix code this test FAILS — `_load_cache` reaches
    `pickle.load` before any guard runs, the reduce fires, and the marker
    appears. That is the mutation, and it is why the version/base-path checks in
    `_load_cache` were never a defence.
    """
    marker = tmp_path / "PWNED"

    class _Payload:
        def __reduce__(self) -> tuple:  # type: ignore[type-arg]
            return (Path.write_text, (marker, "executed"))

    import pickle  # noqa: PLC0415 - the exploit fixture needs it; the service must not

    payload = pickle.dumps({"version": 2, "base_path": str(FIXTURES_PATH), "index": _Payload()})
    (tmp_path / "skills-cache.pkl").write_bytes(payload)
    (tmp_path / "skills-cache.json").write_bytes(payload)

    service = _service(tmp_path)
    service.build_index()

    assert not marker.exists(), "the cache path still executes pickle reduce code"
    assert service.list_categories(), "the index did not rebuild after the poisoned cache"
