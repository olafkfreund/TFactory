# TFactory runtime base: all Chainguard package-index operations happen here.
FROM cgr.dev/chainguard/python:latest-dev@sha256:768ee36ee9f4b8c882634c07d0b889425bc8e100886a3f742b83b1c647cc2d4b AS runtime
LABEL org.opencontainers.image.source="https://github.com/olafkfreund/TFactory"
USER root

# Pull all available Wolfi security patches at build time. The base is pinned
# by digest for reproducibility, but a pinned digest lags behind freshly-
# disclosed CVEs. When the rolling Wolfi repo is ahead of the pinned snapshot,
# `apk upgrade` clears fixable HIGH/CRITICAL findings on each rebuild without a
# digest bump — the between-bumps guard (cf. the binutils constraint below).
# When the snapshot itself lags (the repo has no newer rev yet), bump the base
# digest above to a Chainguard rebuild that ships the fix — that's what cleared
# CVE-2026-45447 (libcrypto3/libssl3 3.6.2-r5 → 3.6.3-r1). Dependabot automates
# the digest bumps; this RUN covers the window in between.
#
# SECURITY_REFRESH busts THIS layer's cache on scheduled and manual base builds.
# Without it, the upgrade layer would be reused indefinitely.
ARG SECURITY_REFRESH=0
RUN echo "security refresh: ${SECURITY_REFRESH}" \
    && apk upgrade --no-cache

# System packages from Wolfi APK index. Build tools come bundled in :latest-dev.
#   git           — worktree operations
#   curl, wget    — downloads (HEALTHCHECK uses curl)
#   gh            — GitHub CLI (Wolfi apk package name)
#   (Node is NOT from apk: see the node-runtime COPY below, Factory#1710.)
#   ca-certificates — TLS roots
#   bash          — entrypoint script (will be removed in P0.3)
#   binutils      — the :latest-dev base bundles binutils 2.46-r1, which carries
#                   CVE-2026-6846 (HIGH, heap overflow in XCOFF linking; fixed in
#                   2.46-r2). Force a build-newer rev to clear the P0.8 Trivy gate
#                   (test_trivy_no_high_critical). Constraint, not =2.46-r2, so
#                   the build stays green when Wolfi revs the package further;
#                   drop this once the base digest ships the fix (Dependabot).
#   bubblewrap    — OS-level bash sandbox for agent commands. Without it the
#                   Claude Agent SDK logs "Sandbox disabled: ... bubblewrap
#                   (bwrap) not installed" and runs commands with NO filesystem
#                   /network enforcement — unacceptable for enterprise use. The
#                   cluster node allows unprivileged user namespaces, so bwrap
#                   can create the sandbox.
#   socat         — required alongside bwrap by the SDK sandbox network-proxy
#                   path; its absence triggers the same warning.
# Two version floors below are CVE remediation, not preference. `apk upgrade`
# earlier in this stage does not clear either, because both packages come from
# the base image layer and must be named explicitly to be pulled forward:
#   libssl3 / libcrypto3 3.6.3-r3 — CVE-2026-14456 (HIGH), fixed in 3.6.3-r5.
#                        Named together because they ship from the same openssl
#                        origin and apk will not move one without the other.
#   busybox 1.37.0-r61 — CVE-2026-38753 (DoS via crafted AWK in awk_sub) and
#                        CVE-2026-38754 (heap overflow in ifsbreakup, shell/ash.c),
#                        both HIGH, both fixed in 1.38.0-r0.
#   wget    1.25.0-r14 — CVE-2026-58471 and CVE-2026-58472, both HIGH, both
#                        fixed in 1.25.0-r15.
#   curl / libcurl-openssl4 8.22.0-r4 — CVE-2026-92392 (HIGH), fixed in
#                        8.23.0-r0. Named together: same origin.
#   ca-certificates-bundle 20260909-r2 — CVE-2026-111943 (HIGH), fixed in
#                        20260909-r3.
#   uv 0.12.22-r0 — CVE-2026-111943 (HIGH, same Go x509 denylist), fixed in
#                        0.13.6-r1. Agents launch MCP servers with uvx.
#   libbrotlicommon1 / libbrotlidec1 / libbrotlienc1 1.2.0-r5 — CVE-2026-111497
#                        (HIGH), fixed in 1.2.0-r7. Named together: same origin.
#   py3-pip-wheel 26.2.1-r2 — CVE-2026-114613 (HIGH), msgpack map-deserialization
#                        leak in pip's vendored msgpack 1.2.1; fixed in 26.2.2-r1
#                        (vendors 1.2.3). python-3.14 depends on it, so it cannot
#                        be apk-deleted with the other pip packages below.
#   libexpat1 2.8.5-r0 — CVE-2026-113351 and CVE-2026-115506, both HIGH, both
#                        fixed in 2.10.0-r0.
#   python-3.14 (+ -base, -base-dev, -dev) 3.14.8_git20261001-r1 — CVE-2026-119170,
#                        HIGH, fixed in 3.14.10_git20261111-r0. Same minor, so
#                        wheel ABI is unchanged (no digest re-pin: that broke
#                        real_ladybug in #1553).
# Floors rather than == pins, so the build stays green as Wolfi revs further
# (1.38.0-r1 and later already exist); drop each once the base digest ships it.
# Verified present in the Wolfi x86_64 APKINDEX, then confirmed against the
# pinned base digest itself — a floor above the newest available version fails
# the build outright.
RUN apk add --no-cache \
        bash \
        "busybox>=1.38.0-r0" \
        "libcrypto3>=3.6.3-r5" \
        "libssl3>=3.6.3-r5" \
        "binutils>2.46-r1" \
        bubblewrap \
        ca-certificates \
        "ca-certificates-bundle>=20260909-r3" \
        "uv>=0.13.6-r1" \
        "curl>=8.23.0-r0" \
        "libcurl-openssl4>=8.23.0-r0" \
        "libbrotlicommon1>=1.2.0-r7" \
        "libbrotlidec1>=1.2.0-r7" \
        "libbrotlienc1>=1.2.0-r7" \
        "py3-pip-wheel>=26.2.2-r1" \
        "libexpat1>=2.10.0-r0" \
        "python-3.14>=3.14.10_git20261111-r0" \
        "python-3.14-base>=3.14.10_git20261111-r0" \
        "python-3.14-base-dev>=3.14.10_git20261111-r0" \
        "python-3.14-dev>=3.14.10_git20261111-r0" \
        git \
        gh \
        gnupg \
        socat \
        "wget>=1.25.0-r15"

# The venv can bootstrap pip from py3-pip-wheel without these base copies.
RUN apk del --no-cache py3.14-pip py3.14-pip-base \
 && ! apk info -e py3.14-pip && ! apk info -e py3.14-pip-base

