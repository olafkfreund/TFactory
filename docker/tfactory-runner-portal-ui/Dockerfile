# syntax=docker/dockerfile:1.7
# tfactory-runner-portal-ui — the portal-ui browser-test runner image (#553).
#
# Exercises a deployed Factory portal behind Keycloak MFA and publishes findings
# into the Visual Inspection store. Built on the official Microsoft Playwright
# Python base (Ubuntu 24.04 + python Playwright + chromium/firefox/webkit + all
# OS-level browser deps baked) — the same proven base family as
# tfactory-runner-playwright, so no nix is needed for the baked image.
#
# Build context = REPO ROOT (the image vendors the portal_testing harness):
#   docker build -f docker/tfactory-runner-portal-ui/Dockerfile \
#                -t ghcr.io/<owner>/tfactory-runner-portal-ui:latest .
#
# Run (k8s Job, dispatch.py builds the manifest):
#   python -m portal_testing.run <portal> --visual-inspection --run-id <id>
FROM mcr.microsoft.com/playwright/python:v1.49.0-noble


WORKDIR /app

# The MS base bakes the browser binaries (/ms-playwright) + OS deps, but NOT the
# python package — install playwright matching the baked browsers, plus pyotp for
# the real TOTP at the Keycloak MFA step. PLAYWRIGHT_BROWSERS_PATH points at the
# baked browsers so nothing is downloaded at runtime.
RUN pip install --no-cache-dir playwright==1.49.0 "pyotp>=2.9"

# Placed AFTER pip, and the PEP 668 marker is removed afterwards.
#
# Upgrading pulls a newer system Python that ships EXTERNALLY-MANAGED, which
# makes `pip install` refuse. Moving the upgrade after the build's own pip was
# not enough: the CI gate does `docker run ... pip install pytest pyyaml`
# INSIDE this image, so a marker left in place breaks it at runtime.
#
# Dropping the marker restores the behaviour this image already had and is
# built around -- it is a disposable test runner where system-wide pip IS the
# intended workflow, not a general-purpose system where PEP 668 protects a
# distro-managed interpreter.
#
# Running last also busts less cache: only this final layer rebuilds daily.
ARG SECURITY_REFRESH=0
RUN echo "security refresh: ${SECURITY_REFRESH}" \
    && apt-get update \
    && apt-get upgrade --yes \
    && rm -rf /var/lib/apt/lists/* \
    && rm -f /usr/lib/python3*/EXTERNALLY-MANAGED
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

# Vendor the harness. `python -m portal_testing.run` resolves it from /app.
COPY portal_testing/ /app/portal_testing/

# The harness writes the Visual Inspection run under $HOME/.tfactory (the Job
# co-mounts the control-plane data PVC there). Default HOME is overridden by the
# Job env; keep the package importable and chromium sandbox-friendly.
ENV PYTHONUNBUFFERED=1 \
    PLAYWRIGHT_SKIP_VALIDATE_HOST_REQUIREMENTS=true

ENTRYPOINT []
CMD ["python", "-m", "portal_testing.run", "all", "--visual-inspection"]
