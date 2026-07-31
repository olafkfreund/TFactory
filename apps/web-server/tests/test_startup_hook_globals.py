"""Every global name server.main's code references must actually resolve (#918).

`sys.path` was referenced inside `lifespan` with no module-scope `import sys`,
so both startup hooks (#774 inline-orphan reconcile, #742 worktree GC) raised
NameError on every boot. The surrounding `except Exception: ... (continuing)`
swallowed it, so the pod logged "Application startup complete", /api/health
returned 200, and neither feature ran for an entire image generation.

Nothing caught it because nothing executes `lifespan` outside a real boot. This
does it statically instead: walk the module's compiled code objects and assert
every LOAD_GLOBAL resolves. Locally-imported names compile to LOAD_FAST, so a
deliberate deferred import is not flagged -- only a name that would raise
NameError the moment its branch runs.
"""

import builtins
import dis
import types

from server import main


def _global_names(code: types.CodeType):
    """Yield (qualified location, global name) for every LOAD_GLOBAL, recursively."""
    for instruction in dis.get_instructions(code):
        if instruction.opname == "LOAD_GLOBAL":
            yield code.co_name, instruction.argval
    for const in code.co_consts:
        if isinstance(const, types.CodeType):
            yield from _global_names(const)


def test_no_unresolvable_global_in_server_main():
    unresolved = [
        (where, name)
        for where, name in _global_names(main.__spec__.loader.get_code("server.main"))
        if not hasattr(main, name) and not hasattr(builtins, name)
    ]
    assert not unresolved, (
        "server.main references global names that do not exist; each raises "
        f"NameError when its branch runs: {unresolved}"
    )


def test_startup_hook_bugs_are_not_swallowed():
    """A NameError/AttributeError in a startup hook must reach the boot, not the log."""
    assert NameError in main._STARTUP_BUGS
    assert AttributeError in main._STARTUP_BUGS
    # ImportError must stay swallowed: an absent apps/backend is by design in
    # dev/test, and re-raising it would turn every non-pod boot into a crash.
    assert ImportError not in main._STARTUP_BUGS


def test_add_backend_to_path_is_importable_and_idempotent():
    import sys

    main._add_backend_to_path()
    before = list(sys.path)
    main._add_backend_to_path()
    assert sys.path == before
