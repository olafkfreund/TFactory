"""Put ``apps/web-server`` on ``sys.path`` for every module in this directory.

Sixteen of the modules here already do this individually, and the ones that do
not -- ``test_tracing.py`` among them -- import ``server.*`` successfully only
because one of those sixteen happened to be collected first. That works for a
full alphabetical run and breaks the moment the order changes: running a single
module, filtering with ``-k``, splitting across xdist workers, or any plugin
that shuffles collection. ``test_tracing.py`` cannot be collected on its own
today (TFactory#1046).

Doing it once here makes the import independent of collection order. The
per-module inserts remain harmless -- they are all guarded by an ``in sys.path``
check -- and can be removed separately.
"""

import sys
from pathlib import Path

_WEB_SERVER = Path(__file__).resolve().parents[1]
if str(_WEB_SERVER) not in sys.path:
    sys.path.insert(0, str(_WEB_SERVER))
