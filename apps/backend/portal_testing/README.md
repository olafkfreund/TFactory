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

## Proven
Live against all four Factory portals (4/4 MFA login). Reports + screenshots +
screencasts in the companion `tfactory-testing` repo.
