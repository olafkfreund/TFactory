# TFactory canonical commands.  Run `just --list` for the full menu.
#
# Categories:
#   - setup:  install / configure
#   - dev:    run locally
#   - docs:   the Docusaurus site at https://dataseeek.github.io/TFactory/
#   - demo:   the end-to-end Claude → portal → agent script
#   - test:   the test suites

# Default — list everything
default:
    @just --list

# ----- setup -----

# Install all deps (backend + frontend + docs)
install:
    npm run install:all
    cd docs && npm install

# Set up the Claude OAuth token
setup-token:
    claude setup-token

# ----- dev -----

# Start the FastAPI web-server (port 3103)
backend:
    cd apps/web-server && .venv/bin/python -m server.main

# Start the Vite dev server (port 3100)
frontend:
    cd apps/frontend-web && npm run dev

# ----- docs -----

# Run the Docusaurus dev server (port 3000)
docs-dev:
    cd docs && npm start

# Build the static docs site
docs-build:
    cd docs && npm run build

# Serve the built docs locally
docs-serve:
    cd docs && npm run serve

# ----- demo -----

# Run the end-to-end demo
demo:
    ./scripts/demo.sh

# Run demo without pauses
demo-yolo:
    ./scripts/demo.sh --yolo

# Refresh portal screenshots (requires running portal + demo state)
screenshots:
    npm -w apps/frontend-web run capture-screenshots

# ----- test -----
#
# The backend pytest recipes run under `nix develop -c` so they get the flake
# devShell's LD_LIBRARY_PATH (stdenv.cc.cc.lib → libstdc++). The async-DB tests
# import SQLAlchemy's greenlet C-extension, which dlopen's libstdc++.so.6 at
# runtime; on NixOS there's no global one, so running the venv pytest OUTSIDE
# the devShell fails with `libstdc++.so.6: cannot open shared object file` and
# every async test errors at setup. `nix develop -c` makes the recipe work
# regardless of whether you're inside direnv. (CI runs pytest directly on an
# ubuntu runner where libstdc++ is present, so ci.yml is unaffected.)

# Backend unit tests (skip slow)
test-backend:
    nix develop -c apps/backend/.venv/bin/pytest tests/ -m "not slow"

# Frontend typecheck
test-frontend:
    cd apps/frontend-web && npm run typecheck

# Postgres acceptance tests (needs Docker)
test-postgres:
    nix develop -c apps/backend/.venv/bin/pytest tests/postgres/ -m postgres -v

# Everything CI runs (slow!)
test-all: test-backend test-frontend test-postgres
    @echo "All tests complete."
