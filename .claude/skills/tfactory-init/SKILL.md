---
name: tfactory-init
description: Scaffold a .tfactory.yml + empty .tfactory/tests-catalog.json in an AIFactory repo so TFactory can start generating tests for it.
when_to_use:
  - First-time TFactory adoption for the current repo
  - User says "set up TFactory", "init tfactory", "/tfactory-init"
  - The repo has no .tfactory.yml yet and the user wants the canonical scaffold
allowed_tools:
  - Read
  - Write
  - Glob
  - Grep
  - Bash
---

# /tfactory-init

Scaffold the two files TFactory needs at the root of an AIFactory repo:

1. **`.tfactory.yml`** — declares the targets (HTTP services, k8s namespaces,
   docker-compose stacks, feature-flag overlays) that tests will exercise,
   plus optional `test_data` seed/reset hooks and an optional
   `evidence_policy` (screenshots/video/HAR retention — Task 16).
2. **`.tfactory/tests-catalog.json`** — the persistent cross-run catalog the
   Triager (Task 11) consults to decide UPDATE-in-place vs CREATE-new per AC.
   Starts empty: `{"version": 1, "updated_at": "<now-Z>", "tests": []}`.

> **What this skill does NOT do:** it does not register the repo with the
> TFactory portal, does not run the pipeline, does not write to any remote.
> It writes two files in the current working directory and validates them.

## When to use

Trigger on:

- explicit `/tfactory-init`
- "set up TFactory in this repo"
- "initialise tfactory"
- "tfactory init"

Do NOT trigger when the user is asking to run a pipeline (use
`handover-to-tfactory`) or to add a single test (use `tfactory-add-test`).

## Procedure

### 1. Confirm the cwd looks like a project root

Use the Read / Glob tool to check for at least one of:

- `package.json`
- `pyproject.toml`
- `go.mod`
- `Cargo.toml`
- `.git/` directory

If none are found, warn the user:

> This directory doesn't look like a project root — no `package.json`,
> `pyproject.toml`, or `.git/` found. Continue anyway? (y/n)

Honour their answer. Do not proceed silently.

### 2. Check whether `.tfactory.yml` already exists

If the file is present, read it and show the user a summary:

```
Found existing .tfactory.yml:
  - 2 targets: api (http), cluster (kubernetes)
  - default_target: api
  - test_data: ./scripts/seed-test-db.sh
Overwrite? (y/N — defaults to no)
```

If they decline, exit cleanly with a status line:
`.tfactory.yml left unchanged. Existing config: <summary>.`

### 3. Interactive target collection

Ask the user, one target at a time:

> What kind of target do you want to add?
>   1. **http**           — HTTP/HTTPS service (REST API or browser app)
>   2. **kubernetes**     — Service inside a k8s cluster
>   3. **docker_compose** — Local stack via docker-compose
>   4. **feature_flag**   — Feature-flag overlay (GrowthBook / LaunchDarkly / Split / Unleash)
>   5. done               — Finish target collection

For each target type, collect:

**http target:**
- `name` (slug, lower-case, e.g. `api` / `web-staging`)
- `base_url` (full https URL)
- auth type? `bearer` / `basic` / `oauth2_client_credentials` / `none`
  - for `bearer`: `token_env` (env-var NAME, e.g. `STAGING_API_TOKEN`)
  - for `basic`: `username_env` + `password_env`
  - for `oauth2_client_credentials`: `token_url` + `client_id_env` + `client_secret_env`
- `health_check`? path + expect_status (default `/healthz` 200)
- `openapi_spec`? path to OpenAPI YAML (optional; helps the Planner for API lane)

**kubernetes target:**
- `name`, `context` (kubectl context), `namespace`
- auth: `serviceaccount` (token_file path) or `mtls` (client_cert + client_key + optional ca_cert)
- optional `service`, `port`, `port_forward` (bool)

**docker_compose target:**
- `name`
- `compose_file` (path relative to repo root, e.g. `docker-compose.test.yml`)
- `services` (list — at least one)
- `wait_for` (list of `{url, timeout_seconds, expect_status}` — typically the
  app's ready endpoint)

**feature_flag target:**
- `name`, `flag_key`, `service` (`growthbook` | `launchdarkly` | `split` | `unleash`)
- auth: `bearer` (token_env is the SDK key env var) or `none`

> **Decision 7 of the v0.2 design spec:** auth fields store the env-var
> **name** (e.g. `token_env: STAGING_API_TOKEN`). NEVER prompt the user for
> the secret value. NEVER write the secret value into `.tfactory.yml`. The
> Executor resolves env-vars at runtime via `tfactory_yml.secrets`.

### 4. Optional global blocks

After targets, ask:

> Add a `default_target`? (choose from the names you just declared, or
> press enter to skip)

> Add `test_data` seed/reset hooks?
>   - `seed_command`: shell command to seed the DB (e.g.
>     `./scripts/seed-test-db.sh`)
>   - `reset_command`: shell command to reset (e.g.
>     `./scripts/reset-test-db.sh`)
>   - `fixtures_dir`: optional path to a fixtures directory

> Add an `evidence_policy`? (Task 16 placeholder — accept any sub-keys; the
> portal will populate this later)

### 5. Render and validate

Render the collected data as a `.tfactory.yml` YAML file matching the
`TFactoryConfig` Pydantic schema (`apps/backend/tfactory_yml/schema.py`).
Example layout:

```yaml
version: 1
targets:
  - name: api
    type: http
    base_url: https://api.staging.example.com
    auth:
      type: bearer
      token_env: STAGING_API_TOKEN
    health_check:
      path: /healthz
      expect_status: 200
default_target: api
test_data:
  seed_command: ./scripts/seed-test-db.sh
```

Before writing the file, validate it by running:

```bash
PYTHONPATH=apps/backend python3 -c "
from pathlib import Path
from tfactory_yml import load_tfactory_yml
load_tfactory_yml(Path('.'))
print('OK')
"
```

(If you're running this skill against a project that doesn't have TFactory
checked out alongside, substitute the absolute path to a TFactory checkout
on the `PYTHONPATH`.) If validation prints `OK`, write the file. If it
raises `TFactoryYmlError`, show the user the error message and ask them
to correct the offending field — do NOT save an invalid config.

### 6. Scaffold the empty tests-catalog

If `.tfactory/tests-catalog.json` does NOT already exist, write:

```json
{
  "version": 1,
  "updated_at": "<UTC ISO-8601 timestamp with Z suffix>",
  "tests": []
}
```

If it already exists, do NOT overwrite — the catalog accumulates entries
across runs and must survive re-init. Just log:

```
.tfactory/tests-catalog.json already present (N entries) — preserved.
```

### 7. Summary + next steps

Print a concise summary:

```
✓ Wrote .tfactory.yml         (2 targets, default=api)
✓ Wrote .tfactory/tests-catalog.json (empty)

Next steps:
  • Add `.tfactory/` to `.gitignore` if you don't want to commit per-spec
    workspace data (but DO commit `.tfactory.yml` and `.tfactory/tests-catalog.json`)
  • Hand a feature off to TFactory:  /handover-to-tfactory
  • Add a single test from a template:  /tfactory-from-template
  • Add a single test by symbol:        /tfactory-add-test
```

## Failure modes

- **`.tfactory.yml` already exists and user declined overwrite** → exit
  cleanly, leave files untouched.
- **Validation error from `load_tfactory_yml`** → surface the
  `TFactoryYmlError` (which carries `.field` + `.reason`) to the user, ask
  for correction, retry validation. Never write an invalid file.
- **Permission denied writing to cwd** → tell the user; do not retry with
  sudo.
- **`PYTHONPATH` doesn't resolve `tfactory_yml`** → skip the strict
  validation step and warn the user that the config was written without a
  schema check; suggest they run `load_tfactory_yml` themselves.

## Non-goals

- Does NOT prompt for or write secret values — only env-var **names**.
- Does NOT register the project with the TFactory portal (use the portal
  UI or `mcp__tfactory__project_create`).
- Does NOT install TFactory or its dependencies.
- Does NOT modify `.gitignore`, `package.json`, or any other repo file
  beyond `.tfactory.yml` + `.tfactory/tests-catalog.json`.
