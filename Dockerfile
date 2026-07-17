# =============================================================================
# TFactory — Chainguard distroless build
# =============================================================================
# Epic #26 (issue #27): port from the legacy Ubuntu Dockerfile to a
# Chainguard base. Each P0 chunk turns one or more tests in tests/docker/
# from skipped → passing.
# =============================================================================

# ---------------------------------------------------------------------------
# Stage 1: Build the React frontend
# ---------------------------------------------------------------------------
# Digest is the OCI image-index (manifest-list) sha256 so multi-arch buildx
# (P0.6) can still resolve the right platform manifest. The `:latest-dev`
# tag is kept alongside the digest as a human hint and is ignored by docker
# when a digest is present. Updates land via Renovate PRs (renovate.json).
FROM cgr.dev/chainguard/node:latest-dev@sha256:64d0788274a7eb5002e09b77570baeb4f8fa34685f8cbccbcb5a2d073b2550dd AS frontend-build

USER root
WORKDIR /build

# Workspace-aware install via root package + the frontend's package.json
COPY package.json package-lock.json ./
COPY apps/frontend-web/package.json apps/frontend-web/

RUN npm ci --workspace=apps/frontend-web

COPY apps/frontend-web/ apps/frontend-web/

# vite.config.ts: build.outDir = '../web-server/static'
RUN mkdir -p apps/web-server/static \
 && cd apps/frontend-web \
 && npm run build

# ---------------------------------------------------------------------------
# Stage 2: Runtime (Chainguard Python, dev variant for now — minimal split
# happens in P0.5 once we know what the runtime *actually* needs)
# ---------------------------------------------------------------------------
FROM cgr.dev/chainguard/python:latest-dev@sha256:bee63d1fd86c4b31dd2df85bb383be142e8067486e3d469265edb850af93e8e4 AS runtime

USER root

# Pull all available Wolfi security patches at build time. The base is pinned
# by digest for reproducibility, but a pinned digest lags behind freshly-
# disclosed CVEs. When the rolling Wolfi repo is ahead of the pinned snapshot,
# `apk upgrade` clears fixable HIGH/CRITICAL findings on each rebuild without a
# digest bump — the between-bumps guard (cf. the binutils constraint below).
# When the snapshot itself lags (the repo has no newer rev yet), bump the base
# digest above to a Chainguard rebuild that ships the fix — that's what cleared
# CVE-2026-45447 (libcrypto3/libssl3 3.6.2-r5 → 3.6.3-r1). Renovate automates
# the digest bumps; this RUN covers the window in between.
RUN apk upgrade --no-cache

# System packages from Wolfi APK index. Build tools come bundled in :latest-dev.
#   git           — worktree operations
#   curl, wget    — downloads (HEALTHCHECK uses curl)
#   gh            — GitHub CLI (Wolfi apk package name)
#   nodejs, npm   — runtime Node for `npm install -g @anthropic-ai/claude-code`
#                   spawned by the agent. Installed via apk instead of
#                   binary-copying from the frontend stage so dynamic linker
#                   deps (libuv etc.) resolve correctly.
#   ca-certificates — TLS roots
#   bash          — entrypoint script (will be removed in P0.3)
#   binutils      — the :latest-dev base bundles binutils 2.46-r1, which carries
#                   CVE-2026-6846 (HIGH, heap overflow in XCOFF linking; fixed in
#                   2.46-r2). Force a build-newer rev to clear the P0.8 Trivy gate
#                   (test_trivy_no_high_critical). Constraint, not =2.46-r2, so
#                   the build stays green when Wolfi revs the package further;
#                   drop this once the base digest ships the fix (Renovate).
#   libexpat1     — the :latest-dev base bundles libexpat1 2.8.1-r1, which
#                   carries CVE-2026-56131/56407/56408 (HIGH; use-after-free +
#                   integer overflows, fixed in 2.8.2-r0). The pinned snapshot
#                   lags so `apk upgrade` can't reach it; force a build-newer rev
#                   to clear the P0.8 Trivy gate. Constraint (not =2.8.2-r0) so
#                   the build stays green as Wolfi revs further; drop once the
#                   base digest ships the fix (Renovate).
#   bubblewrap    — OS-level bash sandbox for agent commands. Without it the
#                   Claude Agent SDK logs "Sandbox disabled: ... bubblewrap
#                   (bwrap) not installed" and runs commands with NO filesystem
#                   /network enforcement — unacceptable for enterprise use. The
#                   cluster node allows unprivileged user namespaces, so bwrap
#                   can create the sandbox.
#   socat         — required alongside bwrap by the SDK sandbox network-proxy
#                   path; its absence triggers the same warning.
RUN apk add --no-cache \
        bash \
        "binutils>2.46-r1" \
        bubblewrap \
        ca-certificates \
        curl \
        git \
        gh \
        gnupg \
        "libexpat1>2.8.1-r1" \
        nodejs \
        npm \
        socat \
        wget

# Epic #44 R3 — optionally bundle the rmux binary.
#
# Build args:
#   WITH_RMUX=false   (default — bank-pilot image; no rmux binary at all)
#   WITH_RMUX=true    (dev/demo image; pins rmux v0.3.1 by SHA-256)
#
# CI matrix builds both: ``tfactory:vX`` (default) and ``tfactory:vX-rmux``.
# Bank-pilot image's Trivy report + Syft SBOM contain no rmux components.
#
# Arch support: only ``x86_64-unknown-linux-gnu`` is available upstream as
# of v0.3.1 (no aarch64 Linux build yet — tracked in Helvesec/rmux roadmap).
# ARM64 builds fail-fast with a clear message rather than silently install
# the wrong binary.
ARG WITH_RMUX=false
ARG RMUX_VERSION=0.3.1
# SHA-256 of rmux-v0.3.1-x86_64-unknown-linux-gnu.tar.gz from upstream
# SHA256SUMS file.  Bump together with RMUX_VERSION on upgrades.
ARG RMUX_SHA256_AMD64=511d3caceea4fcbc1458877a192efffcde5ceb1455f040f1a79c63ab00804cf8
RUN if [ "$WITH_RMUX" = "true" ]; then \
      arch="$(uname -m)"; \
      case "$arch" in \
        x86_64) \
          target="x86_64-unknown-linux-gnu"; \
          sha="${RMUX_SHA256_AMD64}" \
          ;; \
        *) \
          echo "WITH_RMUX=true: unsupported arch '$arch' (rmux v${RMUX_VERSION} ships x86_64 Linux only)" >&2; \
          exit 1 \
          ;; \
      esac; \
      curl -fsSL "https://github.com/Helvesec/rmux/releases/download/v${RMUX_VERSION}/rmux-v${RMUX_VERSION}-${target}.tar.gz" \
           -o /tmp/rmux.tar.gz; \
      echo "${sha}  /tmp/rmux.tar.gz" | sha256sum -c -; \
      mkdir -p /tmp/rmux-extract; \
      tar -xzf /tmp/rmux.tar.gz -C /tmp/rmux-extract; \
      find /tmp/rmux-extract -name rmux -type f -executable -exec install -m 0755 {} /usr/local/bin/rmux \; ; \
      rm -rf /tmp/rmux.tar.gz /tmp/rmux-extract; \
      /usr/local/bin/rmux -V; \
    else \
      echo "rmux integration not bundled (WITH_RMUX=false — bank-pilot image)"; \
    fi

# Project layout (keeping the legacy path under /home/projects for minimum
# diff with the existing Dockerfile; P0.4 may relocate to /app under nonroot)
RUN mkdir -p /home/projects/MagesticAI \
 && chown -R nonroot:nonroot /home/projects

# Copy project sources (respects .dockerignore)
COPY --chown=nonroot:nonroot . /home/projects/MagesticAI/

# Copy built frontend assets from Stage 1
COPY --from=frontend-build --chown=nonroot:nonroot \
    /build/apps/web-server/static/ \
    /home/projects/MagesticAI/apps/web-server/static/

# Drop to nonroot for venv + npm config (writeable paths only)
USER nonroot

# Configure npm global install dir under the nonroot home
RUN mkdir -p /home/nonroot/.npm-global \
 && npm config set prefix /home/nonroot/.npm-global

# Single Python venv shared by web-server and backend scripts (matches
# agent_service.py's sys.executable expectations)
RUN python3 -m venv /home/projects/MagesticAI/.venv

RUN /home/projects/MagesticAI/.venv/bin/pip install --no-cache-dir \
        -r /home/projects/MagesticAI/apps/web-server/requirements.txt \
        -r /home/projects/MagesticAI/apps/backend/requirements.txt

# Git identity for in-container worktree operations
RUN git config --global user.name "TFactory" \
 && git config --global user.email "tfactory@container"

# Persistent data directory
RUN mkdir -p /home/nonroot/.tfactory

# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------
ENV APP_HOST=0.0.0.0 \
    APP_PORT=3102 \
    APP_BACKEND_PATH=/home/projects/MagesticAI/apps/backend \
    APP_PROJECTS_DATA_DIR=/home/nonroot/.tfactory \
    APP_DEFAULT_SHELL=/bin/bash \
    PYTHONUNBUFFERED=1 \
    PATH="/home/nonroot/.npm-global/bin:/home/projects/MagesticAI/.venv/bin:$PATH"

EXPOSE 3102

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:3102/api/health || exit 1

WORKDIR /home/projects/MagesticAI/apps/web-server

# Direct CMD — no shell wrapper. Egress control belongs in K8s NetworkPolicy
# (P4 of Epic #26), not in an entrypoint script. Runs as `nonroot` (uid 65532)
# from this point onwards.
#
# Explicitly clear the entrypoint inherited from cgr.dev/chainguard/python
# (which is `/usr/bin/python`) so `docker run image <cmd>` works portably.
# Absolute path to the venv python so we never depend on PATH ordering.
ENTRYPOINT []
CMD ["/home/projects/MagesticAI/.venv/bin/python", "-m", "server.main"]
