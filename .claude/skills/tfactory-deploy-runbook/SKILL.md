---
name: tfactory-deploy-runbook
description: Guide an operator through TFactory's release and deploy runbook, from version bump to a verified, signed image running in the cluster, and its rollback.
when_to_use: When the user asks how TFactory is released or deployed, why a deploy or release did not happen, how to verify what is running, or how to roll back a bad TFactory build.
---

# TFactory deploy runbook

The workflows are the source of truth: [release.yml](../../../.github/workflows/release.yml),
[deploy.yml](../../../.github/workflows/deploy.yml) and
[deploy-drift.yml](../../../.github/workflows/deploy-drift.yml). [RELEASE.md](../../../RELEASE.md)
summarises them. Where the two differ, follow the workflows.

1. **Land on `dev`.** Feature PRs target `dev`. `main` only receives promotion merges from
   `dev` ([CONTRIBUTING.md](../../../CONTRIBUTING.md)). Nothing deploys from `dev`.
2. **Release (optional, only for a version).** `node scripts/bump-version.js patch|minor|major|<x.y.z>`
   updates `package.json`, `apps/frontend-web/package.json` and `apps/backend/__init__.py`.
   Add a `## <version>` entry to `CHANGELOG.md`. When the root `package.json` version changes on
   `main`, `release.yml` fails unless the CHANGELOG entry exists, the tag `v<version>` is unused
   and the extracted notes are non-empty. Otherwise it creates the tag and GitHub release, builds
   and cosign-signs both images and attaches SBOMs. Nobody tags by hand.
3. **Deploy.** A push to `main` (not `docs/**` or root `*.md`) runs `deploy.yml`: builds
   `ghcr.io/olafkfreund/tfactory:sha-<short>`, signs it with cosign by digest, self-verifies,
   runs `assert-signature-location.sh` (the signature location Kyverno reads), then bumps the
   image and `TFACTORY_IMAGE` in `factory-gitops` `apps/tfactory/manifests`. Argo CD syncs it.
   The `seam-check` job then runs the PARR smoke regression.
4. **Prerequisites (operator-owned secrets).** `GITOPS_PAT` (without it the bump is skipped),
   `GHCR_PAT` or package write access for `GITHUB_TOKEN`, and `FACTORY_TOKEN` (without it
   `seam-check` fails). An agent never mints, reads or prints these.
5. **Verify.**
   - `gh run list -R olafkfreund/TFactory --workflow deploy.yml -L 3`, then `gh run view <id>`.
   - `seam-check` is soft: open the "PARR seam smoke regression" step and read its result. A green
     job can hide an ignored failure.
   - Pin landed: `grep -rn "sha-" <factory-gitops clone>/apps/tfactory/manifests`
     shows `newTag: sha-<short>` and the `TFACTORY_IMAGE` value on that same sha.
   - Signature: `cosign verify --certificate-identity-regexp '^https://github\.com/olafkfreund/TFactory/' --certificate-oidc-issuer https://token.actions.githubusercontent.com ghcr.io/olafkfreund/tfactory@<digest>`.
   - Running (read-only): `kubectl -n factory get deploy tfactory` and the `/api/health` probe the
     chart uses.
   - Drift: `deploy-drift.yml` runs every 30 minutes through the hub workflow and flags a `main`
     commit that never reached the pin. Run it by hand with `gh workflow run deploy-drift.yml`.
6. **Rollback.** There is no rollback command in this repo. The operator re-pins BOTH the
   Deployment image and `TFACTORY_IMAGE` in `factory-gitops` `apps/tfactory/manifests` to the last
   good `sha-<short>` (a revert of the `cd(tfactory): sha-<short>` commit does both) and lets
   Argo CD reconcile. Then repeat step 5.

An agent never pushes to `factory-gitops`, never runs `kubectl apply`, `delete` or `rollout`, and never
touches the live cluster beyond read-only `get` and `logs`. The re-pin is the operator's action.
