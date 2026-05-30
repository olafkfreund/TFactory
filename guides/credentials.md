# Credential Broker — authenticate agents to cloud environments

> TFactory's agents can authenticate to **Google Cloud, AWS, Azure, Kubernetes**
> and generic APIs using credentials pulled from a **vault** (Azure Key Vault,
> AWS Secrets Manager, GCP Secret Manager, HashiCorp Vault) or **local encrypted
> files** (sops / age / agenix) — never baked into the repo. Resolution is
> off by default and gated by an explicit per-project **egress opt-in** with an
> honest egress manifest.
>
> Epic [#62](https://github.com/olafkfreund/TFactory/issues/62). Design:
> `docs/plans/2026-05-30-credential-broker-design.md`.

## TL;DR

```bash
# 1. Reference secrets by a backend-prefixed ref (no values in the repo)
#      env:NAME · sops:file#key · agenix:x.age · vault:path#field
#      azurekv://vault/name · aws-sm://name#field · gcp-sm://proj/secret[/ver]

# 2. Opt into egress in .tfactory.yml (default: OFF -> no cloud creds resolved)
cat >> .tfactory.yml <<'YAML'
egress:
  enabled: true
  destinations:
    - { name: staging-api, host: api.staging.example.com }
credentials:
  gcp:         { ref: "gcp-sm://my-proj/tfactory-sa", as: GOOGLE_APPLICATION_CREDENTIALS, kind: file }
  staging_api: { ref: "vault:secret/data/staging#token", as: STAGING_API_TOKEN }
YAML

# 3. See exactly what would leave your network (secret-free)
python -m tfactory_secrets.cli audit .

# 4. Run the pipeline as usual — the agent inherits the resolved env + cred files.
```

## Secret reference syntax

| Backend | Ref form | Resolves via |
|---|---|---|
| Environment | `env:NAME` | the named env var |
| Local file (plain) | `file:/path[#field]` | read file (optionally a `key: value` field) |
| sops | `sops:file.enc.yaml#key` | `sops -d` |
| age / agenix | `age:f.age` · `agenix:x.age` | `age`/`rage -d -i <identity>` |
| HashiCorp Vault | `vault:secret/data/app#field` | `hvac` (KV-v1/v2), `VAULT_ADDR`/`VAULT_TOKEN` |
| Azure Key Vault | `azurekv://vault-name/secret` | `azure-identity` + `azure-keyvault-secrets` |
| AWS Secrets Manager | `aws-sm://name#json-field` | `boto3`, region from `AWS_REGION` |
| GCP Secret Manager | `gcp-sm://project/secret[/version]` | `google-cloud-secret-manager` (ADC) |

Routing is driven entirely by the ref scheme (`tfactory_secrets.refs.infer_backend_from_ref`).
Cloud SDKs are imported **lazily** — an absent package makes only that backend
unavailable, never breaks TFactory.

## Auth & age identity

Cloud backends reuse each SDK's standard credential chain (the same ambient
sources `core/mcp_credentials.py` probes): Vault token/AppRole, Azure
`DefaultAzureCredential` (SP / Managed Identity / CLI), the boto3 chain (env /
`~/.aws` / profile / IRSA), and GCP ADC. For `age`/`agenix`, the identity is
found from `TFACTORY_AGE_IDENTITY` / `SOPS_AGE_KEY_FILE` / `AGE_IDENTITY_FILE`
or `~/.config/sops/age/keys.txt`.

## Configuration surfaces

- **Per-project** `.tfactory.yml` — `egress:` (opt-in + declared destinations)
  and `credentials:` (named refs → env var, `kind: env|file`). `kind: file`
  writes the value to a **0600** file in a per-task scratch dir and sets the env
  var to that path (kubeconfig, GCP ADC json).
- **Operator** `~/.tfactory/credentials.json` (chmod 600) — maps cloud
  providers to backend refs for the broker's cloud-provider fetch head:
  ```json
  { "cloud": { "gcp": { "ref": "gcp-sm://proj/sa", "as": "GOOGLE_APPLICATION_CREDENTIALS", "kind": "file" } } }
  ```

## Egress posture (honest by default)

- **Default: OFF.** With no `egress.enabled`, the broker resolves **no** cloud
  credentials. Local backends (`env`, `localfile`) never egress.
- **`python -m tfactory_secrets.cli audit`** prints a secret-free manifest:
  every credential, its backend, its egress class + badge (🔒 local / 🏠
  self-hosted / ☁️ managed cloud), and the declared destinations.
- Resolved secret **values are never written to disk unencrypted** (file creds
  are 0600 in a per-task scratch dir, wiped on task end) and are **redacted from
  logs** (`tfactory_secrets.redaction`).

## Tooling

```bash
python -m tfactory_secrets.cli audit [project_dir] [--json]  # egress manifest
python -m tfactory_secrets.cli doctor                        # backend availability
python -m tfactory_secrets.cli resolve <ref> [--allow-egress] # redacted resolve
```

## Programmatic API

```python
from tfactory_secrets.broker import CredentialBroker

with CredentialBroker(project_dir, spec_dir, egress_allowed=True) as broker:
    token = broker.resolve_ref("vault:secret/data/app#token").value
    status = broker.resolve_cloud("gcp")   # CredentialStatus(available, source, env_vars)
    broker.apply_to_env(os.environ)        # merge resolved env for a subprocess
# cred files wiped on exit
```

## Out of scope (fast-follows)

- Injecting credentials into the **test sandbox** (issue #73).
- Short-lived / **workload-identity federation** (OIDC → STS/WIF) (issue #74).
