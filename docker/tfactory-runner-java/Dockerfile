# syntax=docker/dockerfile:1.7
# tfactory-runner-java — Java lane (#237, epic #232)
#
# JDK 21 (Temurin) + Maven. JUnit 5, JaCoCo (coverage) and PIT (pitest,
# mutation) are pulled per-project from pom.xml at test time — they are not
# global installs (Maven resolves them into the project's dependency graph).
#
# Pairs with the framework registry entry:
#   frameworks/junit/descriptor.yaml → runtime.image: tfactory-runner-java:latest
#
# Security posture (matches the other runners):
#   - Non-root user (tfactory)
#   - --network=none at runtime (no egress; the local Maven repo is warmed at
#     build time so offline `mvn -o test` resolves the common plugins)
#   - --read-only rootfs at runtime (/scratch bind-mount is the only rw surface)
#   - --tmpfs /tmp for Maven/Surefire temp files
#
# Build (from repo root):
#   docker build -f docker/tfactory-runner-java/Dockerfile \
#                -t tfactory-runner-java:latest \
#                docker/tfactory-runner-java

FROM eclipse-temurin:21-jdk-jammy AS runner

# ── OS-level deps ─────────────────────────────────────────────────────────
# maven: build + test runner. ca-certificates/git: image-build-time deps.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        maven \
        ca-certificates \
        git \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

ENV MAVEN_OPTS="-Dmaven.repo.local=/home/tfactory/.m2/repository"

# ── Warm the local Maven repo with the common test plugins ────────────────
# Pre-fetch JUnit 5, Surefire, JaCoCo and PIT so an offline `mvn -o` run inside
# the --network=none container can resolve them. Projects still declare their
# own versions; this just primes the cache for the common case.
RUN (id tfactory >/dev/null 2>&1 || useradd -m -u 1002 tfactory) \
    && mkdir -p /repo /scratch /home/tfactory/.m2 \
    && chown -R tfactory:tfactory /repo /scratch /home/tfactory
USER tfactory
RUN mvn -B -q \
        org.apache.maven.plugins:maven-dependency-plugin:3.6.1:get \
        -Dartifact=org.junit.jupiter:junit-jupiter:5.10.2 \
    && mvn -B -q \
        org.apache.maven.plugins:maven-dependency-plugin:3.6.1:get \
        -Dartifact=org.jacoco:jacoco-maven-plugin:0.8.12 \
    && mvn -B -q \
        org.apache.maven.plugins:maven-dependency-plugin:3.6.1:get \
        -Dartifact=org.pitest:pitest-maven:1.16.1 \
    || true   # best-effort warm; the build must not fail if a coordinate moves

WORKDIR /repo
CMD ["mvn", "--version"]

LABEL org.opencontainers.image.source="https://github.com/olafkfreund/TFactory"
LABEL org.opencontainers.image.description="TFactory Java runner — Unit/API lane (JUnit 5 + JaCoCo + PIT)"
LABEL org.tfactory.runner.framework="junit"
LABEL org.tfactory.runner.language="java"
LABEL org.tfactory.runner.lanes="unit,api"
