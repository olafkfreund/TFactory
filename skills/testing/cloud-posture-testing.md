# cloud-posture-testing

> Source: TFactory (internal) | v0.1.0 | License: MIT OR GPL-3.0 | Tags: cspm,cloud-security,prowler,cis,ocsf,aws,gcp,azure,misconfiguration,posture,read-only

---

# Cloud Posture Testing (CSPM)

Use this skill when you need to assess the *security posture* of a cloud account — AWS, GCP, or Azure — through TFactory's read-only CSPM flow: access gate → resource discovery → service-topology diagram → Prowler/CIS misconfiguration scan emitting OCSF findings → accept/flag/reject verdict (`fail_on_severity`) → remediation plan. This is cloud misconfiguration / posture review, NOT application SAST/DAST/fuzzing (which is out of scope, delegated to dedicated security pipelines per DEC-002).

---

When this skill is activated, always start your first response with the 🧢 emoji.

# Cloud Posture Testing (CSPM)

TFactory's cloud flow (epic #133) answers a different question from the app-test pipeline: not "does this code work?" but "is this cloud account configured safely?". It runs strictly read-only — discover, diagram, scan, verdict, recommend — and never mutates a single cloud resource. This skill covers the five stages, how the verdict is computed, and the hard line between cloud *posture* (CSPM) and application security testing.

---

## When to use this skill
- Auditing an AWS account / GCP project / Azure subscription for misconfigurations (public buckets, over-broad IAM, unencrypted volumes, open security groups).
- Producing a CIS-benchmark posture verdict + remediation plan for a cloud target.
- Wiring a `cloud_provider` target in `.tfactory.yml` and choosing a `fail_on_severity`.
- Reading OCSF-format findings out of `findings/` and ranking them.
- Building or reading the service-topology diagram for an account.
- Do NOT trigger for: application code SAST/DAST/fuzzing (out of scope — DEC-002), generating unit/browser/api tests for app code (the v0.2 lane spine), or anything that *changes* cloud resources (this flow is read-only by design).

---

## Key principles
1. **Read-only, always** — the entire flow is non-mutating. It discovers, scans, and recommends; it never applies a fix. Remediation is a *plan*, executed by humans or a separate pipeline.
2. **Posture ≠ app security** — CSPM checks how the cloud is *configured*; SAST/DAST checks the *application code*. The latter is explicitly out of scope (DEC-002) — delegate it, don't generate it here.
3. **Access gate first** — nothing runs until the read-only access gate confirms credentials with the right (read-only) scope. A scan with write-capable creds is a misconfiguration in itself.
4. **OCSF is the finding lingua franca** — Prowler/CIS results normalize to OCSF (Open Cybersecurity Schema Framework) so findings are consistent and machine-rankable across the three clouds.
5. **The verdict is severity-gated** — `fail_on_severity` decides reject vs flag vs accept. A single CRITICAL above the threshold is enough to reject the whole posture.
6. **Three clouds, one flow** — AWS, GCP, and Azure all go through the same access → discover → topology → scan → verdict → remediation shape (all live-verified).
7. **Discovery feeds the diagram, the diagram feeds triage** — the topology diagram isn't decoration; it shows blast radius and which findings sit on internet-facing paths.
8. **Point-in-time, not continuous** — a posture verdict describes the account *at scan time*. Drift starts the moment operators change something. Re-run on a cadence and after every remediation; never treat a stale report as the current truth.
9. **Provider-agnostic findings, provider-specific fixes** — the OCSF finding ("public object storage") is portable across clouds, but the remediation step is provider-specific (S3 block-public-access vs GCS uniform bucket-level access vs Azure Blob network rules). Triage on the normalized type; remediate per provider.

---

## Core concepts
**The five-stage flow** — the whole epic #133 pipeline is a fixed sequence: access gate → discovery → topology diagram → Prowler/CIS scan → verdict → remediation plan. Each stage feeds the next; you can't scan what you haven't discovered, and you can't sensibly triage findings without the topology. The same shape runs for all three clouds.

**Access gate** — the first stage. Validates that supplied credentials authenticate and are scoped read-only for the target account/project/subscription before any discovery runs.

**Discovery** — enumerates resources across the account (compute, storage, IAM/RBAC, network, data stores). The inventory is the substrate for both the diagram and the scan.

**Service-topology diagram** — a generated graph of discovered services and their relationships (e.g. which compute can reach which data store, what's internet-exposed). Used to prioritize findings by blast radius.

**Prowler / CIS scan** — runs Prowler checks mapped to CIS benchmarks against the discovered resources, emitting misconfiguration findings (public S3/GCS/Blob, wildcard IAM, disabled encryption, open 0.0.0.0/0 ingress, no MFA, stale keys).

**OCSF findings** — the normalized output schema. Each finding carries a severity, the affected resource, the failed control, and remediation guidance — written into `findings/`.

**Verdict (`fail_on_severity`)** — accept / flag / reject. Findings at or above `fail_on_severity` push toward reject; lower-severity ones flag; a clean scan accepts.

**Remediation plan** — an ordered, read-only set of recommended changes (least-privilege IAM, enable encryption, close ingress) — a plan to hand to operators, never auto-applied.

**`.tfactory.yml` cloud_provider target** — declares the cloud target and scan parameters so the flow knows which account/provider to assess and what severity gates the verdict.

**CSPM vs the app-test pipeline** — TFactory runs two distinct flows. The 5-lane app pipeline (unit/browser/api/integration/mutation) verifies *code behavior*; the cloud flow verifies *infrastructure configuration*. They share the accept/flag/reject verdict vocabulary but nothing else — different inputs (a cloud account vs a feature branch), different tooling (Prowler vs pytest/Jest/Playwright), different mutation model (none — cloud is read-only). Don't try to express a cloud target as an app lane or vice versa.

**Verdict vocabulary reuse** — accept (clean / only sub-threshold findings), flag (real findings below `fail_on_severity` worth a human's eyes), reject (one or more findings at/above the gate). This mirrors the Evaluator's verdicts so a posture result reads consistently alongside test results.

**Launch surfaces** — the flow is reachable from the portal (+Task → Cloud Infrastructure) and the `/cloud-discover` skill. Both drive the identical five-stage pipeline; the portal just gives a visual topology and findings view. See `guides/cloud-testing.md`.

---

## Common tasks
### Run the full five-stage assessment, narrated
Trace one AWS assessment through every stage so you know what to expect at each:
1. **Access gate** — the supplied read-only role authenticates; the gate confirms it can't write. If it could, that itself is flagged.
2. **Discovery** — enumerates S3, EC2, IAM, VPC, RDS across the region; builds the inventory.
3. **Topology** — renders the service graph: which EC2 instances sit in public subnets, which S3 buckets are reachable, IAM trust paths.
4. **Prowler/CIS scan** — runs checks against the inventory; emits OCSF findings (a public bucket, a wildcard IAM policy, an unencrypted volume).
5. **Verdict** — with `fail_on_severity: high`, the wildcard IAM (HIGH) drives a `reject`; the unencrypted dev volume (MEDIUM) only flags.
6. **Remediation plan** — ordered recommendations: scope the IAM policy, enable EBS encryption, set S3 block-public-access — all read-only suggestions.
Everything lands in `findings/`; nothing in the account changes.

### Declare a cloud target in `.tfactory.yml`
```yaml
cloud_provider:
  type: aws            # aws | gcp | azure
  region: eu-west-1
  scan:
    benchmark: cis      # CIS benchmark profile
    fail_on_severity: high   # critical | high | medium | low
  access:
    mode: read-only     # access gate enforces this
```

### Run a read-only assessment
Launch from the portal (+Task → Cloud Infrastructure) or the `/cloud-discover` flow. The pipeline runs access gate → discovery → topology → Prowler/CIS → verdict → remediation and writes OCSF findings + the diagram + the plan into `findings/`.

### Read and rank OCSF findings
Findings land in `findings/`. Rank by severity, then by whether the affected resource is internet-facing in the topology diagram (blast radius).
```bash
cat findings/cloud_findings.json | python -m json.tool | grep -E '"severity"|"resource"'
```

### Scope the credential to the target only
The flow discovers everything its credential can see. To assess exactly one account/project/subscription, hand it a read-only principal scoped to that boundary — not an org-wide reader that pulls in siblings:
```yaml
cloud_provider:
  type: gcp
  project: my-target-project       # discovery is bounded to this project
  access: { mode: read-only }
```
A too-broad principal both slows discovery and produces findings for resources you didn't mean to assess.

### Distinguish CSPM from app security in a report
When presenting results, label them clearly as *posture* (configuration) so readers don't expect application vulnerabilities. A line like "No CSPM misconfigurations above HIGH; application SAST/DAST is covered by the dedicated security pipeline (out of scope here per DEC-002)" prevents the common misread that a clean posture means a secure application.

### Prioritize a long findings list
A large account can return dozens of findings. Triage with a two-axis sort rather than severity alone:
1. **Severity** from the OCSF finding (CRITICAL/HIGH first).
2. **Exposure** from the topology diagram (internet-facing > internal-only).
A HIGH finding on an internet-facing resource outranks a CRITICAL on an isolated internal one for remediation order, because exposure determines exploitability. Group remaining ties by OCSF finding type so operators fix a whole class at once.

### Verify the read-only contract held
After any run, confirm nothing was mutated — the flow's core promise. There should be no create/update/delete actions in the assessment's audit trail; every step is a describe/list/get. If you see write calls, the credential was over-scoped or the wrong flow ran. The remediation plan must remain a document, not an applied change.

### Hand off to operators cleanly
The deliverable is the triple (verdict, ranked OCSF findings, remediation plan). Operators apply fixes through their own IaC/pipeline; TFactory re-runs to confirm. Frame the handoff so it's clear which findings block (at/above `fail_on_severity` → drove the reject) versus which merely flag, so operators fix the blockers first.

### Choose a `fail_on_severity`
- Production account, compliance-driven → `fail_on_severity: high` (CRITICAL/HIGH reject).
- Dev sandbox → `medium` or `low` so you still surface drift but don't hard-reject.
- The threshold is the single biggest lever on accept vs reject — set it deliberately.

### Turn findings into a remediation plan
The flow emits a remediation plan automatically. Validate it stays read-only: every item should be a *recommendation* (enable encryption, scope IAM down), never an applied change.

### Triage a multi-cloud assessment consistently
When you assess AWS, GCP, and Azure for the same org, don't compare raw control IDs across clouds — they differ. Group findings by OCSF finding type (e.g. "public object storage", "over-permissive identity") so an Azure public Blob and an AWS public S3 bucket land in the same triage bucket. Then rank within each bucket by severity and topology exposure.

### Confirm the verdict matches the gate
After a run, sanity-check the verdict against `fail_on_severity`: if the gate is `high` and the report contains a HIGH finding, the verdict must be `reject`. A `flag`/`accept` verdict with an at-or-above-threshold finding is a misconfiguration of the gate (or a parse bug) — investigate before trusting it.

### Re-run after operators remediate (out of band)
Because the flow never mutates, the loop is: assess → hand the remediation plan to operators → they apply fixes via their own IaC/pipeline → re-run the assessment to confirm the verdict improved. TFactory's role is the assessment bookends, not the fix.

---

## Gotchas
1. **Write-capable creds defeat the point** — if the access gate is handed admin creds, the scan still works but you've granted a CSPM tool mutate power. Always supply a read-only principal; the gate enforces mode but you choose the IAM role.
2. **CSPM is not SAST/DAST** — teams routinely expect "cloud testing" to find SQL injection in their app. It won't — that's app security, out of scope (DEC-002). Route app vulns to the dedicated pipeline.
3. **`fail_on_severity` too low floods you** — setting it to `low` on a large account produces hundreds of flags; triage fatigue follows. Start high and lower deliberately.
4. **Discovery scope is account-wide** — the flow enumerates everything the creds can see; a broad principal pulls in resources you didn't mean to assess. Scope the credential to the target.
5. **OCSF severity ≠ exploitability** — a CRITICAL public bucket may hold nothing sensitive; a MEDIUM IAM finding may be the real risk. Cross-reference the topology diagram for blast radius before acting.
6. **Three clouds, different control names** — the same concept (public storage) maps to different CIS controls per provider. Don't hard-match control IDs across AWS/GCP/Azure; match on the OCSF-normalized finding type.
7. **Stale assessment after remediation** — operators fix things out of band, but the old report still shows the findings. A posture verdict is a point-in-time snapshot; always re-run after remediation rather than assuming the plan was applied correctly.
8. **Assuming a flag means "safe enough"** — a `flag` only means "below your chosen gate", and the gate is a policy choice. A flagged MEDIUM IAM finding on an internet-facing identity can be the real breach path. Always cross-read flags against the topology before dismissing them.

---

## Anti-patterns / common mistakes
| Mistake | Why it's wrong | What to do instead |
|---|---|---|
| Expecting CSPM to catch app vulns | Posture ≠ application security (DEC-002) | Route SAST/DAST/fuzz to the dedicated security pipeline |
| Running with admin/write creds | A read-only audit tool gains mutate power | Use a read-only principal; let the access gate verify scope |
| Auto-applying the remediation plan | The flow is read-only by contract | Hand the plan to operators; never mutate from this flow |
| Setting `fail_on_severity: low` everywhere | Drowns triage in low-value flags | Gate high in prod, lower only deliberately in sandboxes |
| Ranking findings by severity alone | Severity ignores blast radius/exposure | Cross-reference the topology diagram for internet-facing paths |
| Matching CIS control IDs across clouds | Control IDs differ per provider | Match on OCSF-normalized finding type, not raw control ID |
| Skipping the topology diagram | You lose the exposure context for triage | Use the diagram to prioritize internet-reachable findings |
| Treating a clean scan as "secure" | CSPM checks config, not running app behavior | Pair posture results with app-level security from other pipelines |
