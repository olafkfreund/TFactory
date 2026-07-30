# portal_testing — exploratory MFA'd portal UI testing (capability for #553)

TFactory's browser-test capability for **deployed portals behind Keycloak**.
Drives a portal's `/login` → "Sign in with SSO" → Keycloak username/password →
**TOTP MFA**, then exercises **every nav item, dropdown, and dialog**, capturing
**screenshots + a screencast** and emitting a per-portal findings report.
Findings become **GitHub issues** via `github_flow`.

Registered as the `portal-ui` framework (browser lane) in `frameworks/portal-ui/`.

## Substrate (why nix)
A portal's bundled Chromium needs libs absent on NixOS / in the Wolfi pod (the
gap this closes). `flake.nix` provides Python+Playwright+browsers from nixpkgs
(`playwright-driver.browsers`) — the proven `nix_provisioner` pattern (RFC-0005).
In-cluster it runs as a k8s Job (no container runtime in the pod).

## Use
```sh
# one-time: enroll a TOTP test user in the Keycloak `factory` realm
python -m portal_testing.keycloak_provision        # prints TEST_USER/PASSWORD/TOTP_SECRET

export TEST_USER=... TEST_PASSWORD=... TEST_TOTP_SECRET=...
nix develop --command python -m portal_testing.run all     # pfactory|aifactory|tfactory|cfactory
python -m portal_testing.github_flow olafkfreund/<repo>    # findings -> tracking issues
```

## The runner image (and how it stops going stale)

In-cluster the Job runs `docker/tfactory-runner-portal-ui/Dockerfile`, which
vendors this directory (`COPY portal_testing/ /app/portal_testing/`). So a change
here only reaches the cluster once that image is rebuilt.

`.github/workflows/portal-ui-runner-image.yml` does that on any push to `main`
touching `portal_testing/**` or `docker/tfactory-runner-portal-ui/**`. It runs
this directory's own test suite *inside the built image* before publishing, so an
image carrying a stale harness cannot reach the registry, then bumps the Job's
pin.

Nothing built it for the first five weeks of its life (#886). `:latest` sat on a
27 June build while #875/#876/#877 were merged and green, so the lane in the
cluster kept joining the `tfactory` Service and 502ing the portal it was testing.
Hence the pin below: a floating tag cannot be told apart from a current one.

| Variable | Default | Purpose |
|---|---|---|
| `PORTAL_UI_IMAGE` | `ghcr.io/olafkfreund/tfactory-runner-portal-ui:latest` | Runner image for the dispatched Job. The cluster sets this to an immutable `:sha-<short>` tag (`factory-gitops` `apps/tfactory/manifests`, bumped by the workflow above) so the running code is identifiable by commit. Unset = the floating `:latest` default, which is fine for a local one-off and wrong for the lane. |
| `PORTAL_UI_MFA_SECRET` | `portal-ui-test-user` | Secret holding `TEST_USER` / `TEST_PASSWORD` / `TEST_TOTP_SECRET`, injected via `secretKeyRef` (never argv). |
| `TFACTORY_NAMESPACE` | `factory` | Namespace the Job is created in. |
| `TFACTORY_DATA_PVC` | `tfactory-data` | PVC co-mounted at `~/.tfactory` so the published run lands in the Visual Inspection store. The live Deployment mounts `tfactory-data-rwx`; see #875. |

## Proven
Live against all four Factory portals (4/4 MFA login). Reports + screenshots +
screencasts in the companion `tfactory-testing` repo.
