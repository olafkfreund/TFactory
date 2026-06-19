# syntax=docker/dockerfile:1.7
# tfactory-runner-selenium — Browser lane (Python).
#
# Python 3.13 + selenium 4.x + a real Chromium and its matching chromedriver, so
# Selenium WebDriver tests run headless without a network 'driver install' step.
# Pairs with the framework registry entry:
#   frameworks/selenium/descriptor.yaml → runtime.image: tfactory-runner-selenium:latest
#
# Security posture mirrors the other runners: non-root (uid 1000), and the lane
# runs --read-only with /scratch as the only rw surface. The browser lane needs
# egress to reach the app, so DockerRunner flips the network on per run.
FROM python:3.13-slim AS runner

# Chromium + the matching chromedriver come from Debian so their versions always
# agree (the classic Selenium "session not created: version mismatch" trap).
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        tini \
        ca-certificates \
        chromium \
        chromium-driver \
        fonts-liberation \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    CHROME_BIN=/usr/bin/chromium CHROMEDRIVER=/usr/bin/chromedriver
RUN pip install --no-cache-dir \
    "selenium==4.*" \
    "pytest==8.3.*" \
    "pytest-timeout==2.3.*"

RUN useradd -m -u 1000 tfactory \
    && mkdir -p /repo /scratch \
    && chown tfactory:tfactory /repo /scratch
USER tfactory
WORKDIR /repo
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python", "-m", "pytest", "--help"]

LABEL org.opencontainers.image.source="https://github.com/olafkfreund/TFactory"
LABEL org.opencontainers.image.description="TFactory Selenium runner — Browser lane (Python)"
LABEL org.tfactory.runner.framework="selenium"
LABEL org.tfactory.runner.language="python"
LABEL org.tfactory.runner.lanes="browser"
