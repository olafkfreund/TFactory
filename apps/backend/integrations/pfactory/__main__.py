"""``python -m integrations.pfactory`` — classify an issue / requirements.json
for TFactory pickup (#195). See ``pickup._main`` for usage."""

from .pickup import _main

if __name__ == "__main__":
    raise SystemExit(_main())
