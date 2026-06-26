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

# pyotp mints the real TOTP for the Keycloak MFA step. Playwright + browsers are
# already in the base.
RUN pip install --no-cache-dir pyotp>=2.9

# Vendor the harness. `python -m portal_testing.run` resolves it from /app.
COPY portal_testing/ /app/portal_testing/

# The harness writes the Visual Inspection run under $HOME/.tfactory (the Job
# co-mounts the control-plane data PVC there). Default HOME is overridden by the
# Job env; keep the package importable and chromium sandbox-friendly.
ENV PYTHONUNBUFFERED=1 \
    PLAYWRIGHT_SKIP_VALIDATE_HOST_REQUIREMENTS=true

ENTRYPOINT []
CMD ["python", "-m", "portal_testing.run", "all", "--visual-inspection"]
