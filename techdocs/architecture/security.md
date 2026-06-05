# Security model

TFactory runs untrusted, LLM-generated code and talks to GitHub, cloud accounts and
LLM providers. Security is layered.

## Three-layer defense (agent execution)

1. **OS sandbox** — Bash command isolation.
2. **Filesystem permissions** — operations restricted to the project directory.
3. **Command allowlist** — a *dynamic* allowlist derived from project-stack analysis
   (`core/security.py` + `context/project_analyzer.py`), cached in
   `.tfactory-security.json`.

## Sandboxed test execution

The Executor runs every generated test in a Docker container started
`--network=none --read-only`. Tests cannot reach the network or write outside the
scratch volume. Coverage/junit XML is the only output surface.

## No automatic pushes

The "no automatic pushes" policy is enforced by making **every** outward Git/PR
side-effect dry-run by default:

| Side-effect | Helper | Opt-in flag |
|-------------|--------|-------------|
| Commit tests to the feature branch | `tools/git_writer.py` | `TFACTORY_TRIAGER_GIT_WRITE=1` |
| Post the triage PR comment | `tools/pr_comment.py` | `TFACTORY_TRIAGER_PR_COMMENT=1` |
| Send the AIFactory handback | `agents/handback/` | `TFACTORY_HANDBACK_SEND=1` |
| Completion webhook POST | Triager | `TFACTORY_COMPLETION_WEBHOOK=<url>` |

## Web-server auth & secrets

- **AuthN:** Bearer **JWT** (`python-jose`), password hashing via `passlib[bcrypt]`,
  optional **OIDC** (`authlib`, `APP_OIDC_ENABLED`). A bootstrap admin token is
  printed on first run and saved to `~/.tfactory/.token`.
- **API keys:** scoped MCP access via `acw_`-prefixed keys.
- **Secret encryption at rest:** envelope encryption with a pluggable KMS backend —
  AWS KMS (`boto3`), HashiCorp Vault (`hvac`), Azure Key Vault (`azure-keyvault-keys`)
  or Google Cloud KMS (`google-cloud-kms`).
- **Credential stores:** Git credentials and test-target login credentials are
  managed through dedicated, encrypted routes (`/api/git-credentials`,
  `/api/test-credentials`).
- **GDPR:** erasure + audit-export routes exist (`/api/users/{id}` DELETE,
  `/api/orgs/{org_id}/audit`).

## Data-egress posture (BYO-LLM / air-gapped)

`byo_llm.py` classifies a model+endpoint's egress posture
(**LOCAL / SELF_HOSTED / MANAGED_CLOUD**) so the portal/CLI can show an honest
"🔒 Local — no data egress" badge. `python apps/backend/byo_llm.py <model>` exits 0
only when the run keeps all data on your network. See `guides/byo-llm.md`.
