"""`python -m agents.regression` entry point — RFC-0018 #484."""

from __future__ import annotations

import logging

from .cli import main

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(main())
