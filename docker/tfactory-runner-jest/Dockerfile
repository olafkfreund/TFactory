# syntax=docker/dockerfile:1.7
# tfactory-runner-jest — Task 7 (#23), sub-task 7.2
#
# Node 22 + Jest 29 (LTS series) + ts-jest + @types/jest + nyc (coverage) +
# ESLint 9 + TypeScript 5.6 pre-installed globally.
#
# Pairs with the framework registry entry:
#   frameworks/jest/descriptor.yaml → runtime.image: tfactory-runner-jest:latest
#
# Security posture:
#   - Non-root user (tfactory, uid 1000)
#   - --network=none at runtime (no egress)
#   - --read-only rootfs at runtime (/scratch bind-mount is the only rw surface)
#   - --tmpfs /tmp so Jest's temp files don't hit the ro rootfs
#
# Build (from repo root):
#   docker build -f docker/tfactory-runner-jest/Dockerfile \
#                -t tfactory-runner-jest:latest \
#                docker/tfactory-runner-jest
#
# Run (DockerRunner pattern):
#   docker run --rm \
#       --network=none \
#       --read-only \
#       --tmpfs /tmp:rw,size=64m \
#       -v <repo>:/repo:ro \
#       -v <scratch>:/scratch:rw \
#       --cpus=2 --memory=2g --pids-limit=512 \
#       tfactory-runner-jest:latest \
#       jest --ci --forceExit ...

FROM node:22-slim AS runner

# ── OS-level deps ─────────────────────────────────────────────────────────
# ca-certificates: needed at image-build time (npm install) even though
#       tests run with --network=none at container runtime.
# git: transitive dep for some Jest plugins and test utilities.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        git \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

ENV NODE_ENV=test CI=true

# ── Node.js deps (global) ─────────────────────────────────────────────────
# All pinned to major versions with minor-version float.
# jest@29: LTS series at v0.2 release time; descriptor allows through 31.x.
# ts-jest@29: TypeScript transformer for Jest; must match jest major.
# @types/jest@29: Type definitions; must match jest major.
# nyc@17: Istanbul CLI for coverage instrumentation and LCOV output.
# eslint@9: Flat-config ESLint (Decision 11: flake-lint analog for TypeScript).
# typescript@5.6: Compiler; aligns with ts-jest requirements.
# @typescript-eslint/parser@8 + @typescript-eslint/eslint-plugin@8:
#   TypeScript-aware linting rules for the Gen-Functional guardrail.
RUN npm install -g \
    jest@29 \
    @types/jest@29 \
    ts-jest@29 \
    nyc@17 \
    eslint@9 \
    typescript@5.6 \
    @typescript-eslint/parser@8 \
    @typescript-eslint/eslint-plugin@8 \
    && npm cache clean --force

# ── Filesystem layout ─────────────────────────────────────────────────────
# /repo    — bind-mounted ro at runtime (target project code)
# /scratch — bind-mounted rw at runtime (jest-junit output, lcov reports)
# node:22-slim already ships a "node" user at UID 1000, so create tfactory at
# 1002 (guarded) rather than colliding with it.
RUN (id tfactory >/dev/null 2>&1 || useradd -m -u 1002 tfactory) \
    && mkdir -p /repo /scratch \
    && chown tfactory:tfactory /repo /scratch

USER tfactory
WORKDIR /repo

CMD ["jest", "--help"]

LABEL org.opencontainers.image.source="https://github.com/olafkfreund/TFactory"
LABEL org.opencontainers.image.description="TFactory Jest runner — Unit lane (TypeScript)"
LABEL org.tfactory.runner.framework="jest"
LABEL org.tfactory.runner.language="typescript"
LABEL org.tfactory.runner.lanes="unit"
