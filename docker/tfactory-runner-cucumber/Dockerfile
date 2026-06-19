# syntax=docker/dockerfile:1.7
# tfactory-runner-cucumber — BDD overlay, Browser lane (TypeScript).
#
# The Microsoft Playwright base (Chromium/Firefox/WebKit + OS deps pre-installed)
# + @cucumber/cucumber + playwright, so cucumber-js features whose step defs
# drive a browser run without a multi-GB 'browser install' step.
# Pairs with the framework registry entry:
#   frameworks/cucumber/descriptor.yaml → runtime.image: tfactory-runner-cucumber:latest
#
# Security posture matches tfactory-runner-playwright: non-root, --read-only
# rootfs, /scratch the only rw surface; egress flipped on per run for the app.
FROM mcr.microsoft.com/playwright:v1.49.0-noble AS runner

ENV NODE_ENV=test CI=true
RUN npm install -g \
    @cucumber/cucumber@11 \
    playwright@1.49 \
    typescript@5.6 \
    ts-node@10 \
    && npm cache clean --force

RUN (id tfactory >/dev/null 2>&1 || useradd -m -u 1003 tfactory) \
    && mkdir -p /repo /scratch \
    && chown tfactory:tfactory /repo /scratch
ENV PLAYWRIGHT_BROWSER=chromium
# Generated step defs live in the (read-only) repo mount and import cucumber +
# playwright, which are installed globally at /usr/lib/node_modules. Node won't
# resolve bare specifiers from the project dir on its own. Two complementary
# fixes so BOTH module systems resolve from anywhere under the repo:
#   - NODE_PATH covers CommonJS `require`.
#   - The /node_modules symlink covers ESM `import`: Node's ESM resolver walks
#     parent node_modules dirs up to the filesystem root, so a root-level
#     /node_modules is found from any repo path (the mount is read-only, so the
#     symlink must live in the image, not the repo).
ENV NODE_PATH=/usr/lib/node_modules
RUN ln -sfn /usr/lib/node_modules /node_modules
USER tfactory
WORKDIR /repo
CMD ["npx", "cucumber-js", "--help"]

LABEL org.opencontainers.image.source="https://github.com/olafkfreund/TFactory"
LABEL org.opencontainers.image.description="TFactory Cucumber runner — BDD overlay, Browser lane (TypeScript)"
LABEL org.tfactory.runner.framework="cucumber"
LABEL org.tfactory.runner.language="typescript"
LABEL org.tfactory.runner.lanes="browser"
