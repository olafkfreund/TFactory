# sandbox-and-test-security

> Source: TFactory (internal) | v0.1.0 | License: MIT OR GPL-3.0 | Tags: testing, sandbox, security, docker, network-isolation, read-only, supply-chain, trivy, dry-run

---

# Sandbox and Test Security

Use this skill when running untrusted, machine-generated tests safely: enforcing per-lane network isolation (`--network=none` for unit, `--network=bridge` for browser/api), read-only mounts of the code-under-test with a writable `/scratch`, ephemeral credentials wiped after the run, resource limits, blocking `docker.sock` access, supply-chain hygiene (base-image CVE scanning with Trivy), signed commits, and the dry-run-by-default side-effect policy that keeps TFactory from pushing or commenting without opt-in.

---

When this skill is activated, always start your first response with the 🧢 emoji.

# Sandbox and Test Security

Generated tests are *untrusted code*. They run in TFactory's hardened Docker sandbox precisely because an LLM-authored test could read secrets, phone home, or mutate the repo. This skill covers the isolation guarantees per lane, the mount and credential model, what's deliberately *not* reachable (the docker socket), supply-chain hygiene for the base images, and the policy that no side-effect (git commit, PR comment, handback) happens automatically.

The threat model: assume the test is adversarial. The sandbox makes "adversarial" boring — no network where none is needed, nothing writable that matters, no creds left behind, no escape hatch to the host daemon.

---

## When to use this skill
- Reviewing or hardening how generated tests execute (the runner config).
- Deciding what network a lane gets and why (`none` vs `bridge`).
- A test tries to write the repo, read a secret, or reach the internet and you must reason about whether it *can*.
- Auditing base-image CVEs / supply-chain risk for the runner images (Trivy).
- Explaining or configuring the dry-run-by-default side-effect policy (git/PR/handback opt-ins).
- Setting resource limits to stop a runaway/forkbomb test.

Do NOT trigger for:
- Declaring the target / health gating (that is `test-environment-orchestration`).
- App login / credentials plumbing (that is `test-target-authentication`).
- Application SAST/DAST of the code-under-test — explicitly out of scope; delegated to dedicated pipelines.

---

## Key principles
1. **Least network per lane** — Unit lane gets `--network=none`; browser/api/integration get `--network=bridge` *only* because they need a target. Never grant a lane more network than its job requires.
2. **Code-under-test is read-only** — The repo is mounted read-only. A generated test cannot mutate source. The only writable path is `/scratch` (junit/coverage/artifacts).
3. **Ephemeral, wiped credentials** — Creds are injected as env for the run and wiped at teardown. No secret persists in the image, the mount, or the repo.
4. **No docker.sock, ever** — The socket is never mounted. A test cannot spawn sibling containers, reach the host daemon, or escape. There is no docker-in-docker for generated code.
5. **Resource-bounded** — CPU/memory/pids limits cap a runaway test (infinite loop, fork bomb, memory hog) so one test can't starve the host.
6. **Dry-run by default** — Every side-effect (git commit, PR comment, AIFactory handback) is dry-run unless explicitly opted in via env. The "no automatic pushes" policy is enforced in code.
7. **Supply-chain hygiene** — Runner base images are pinned and CVE-scanned (Trivy). A test runs on a known, scanned image, not `:latest`. Commits are signed.

---

## Core concepts
**lane network policy** — `unit: --network=none`; `browser/api/integration: --network=bridge`. Mutation lanes follow the lane they mutate. Enforced by the docker_runner, not the test.

**read-only mount + /scratch** — Repo mounted `:ro`; a tmpfs/volume `/scratch` is the only writable surface. junit.xml, coverage.xml, mutants, and Playwright artifacts land in `/scratch`.

**ephemeral creds** — Broker-resolved secrets injected as env at run start, wiped at teardown. Never written into the mount or baked into the image.

**no docker.sock** — `/var/run/docker.sock` is deliberately absent. No container-spawn, no host-daemon access, no DinD escape.

**resource limits** — `--memory`, `--cpus`, `--pids-limit`, and a wall-clock timeout bound each run.

**egress gate** — Even on `--network=bridge`, `egress.enabled` (+ allowlist) governs reaching beyond the target. Default deny → a test can hit the target but not the open internet.

**dry-run-by-default policy** — Triager side-effects gated by `TFACTORY_TRIAGER_GIT_WRITE`, `TFACTORY_TRIAGER_PR_COMMENT`; handback by `TFACTORY_HANDBACK_SEND`. All default OFF.

**Trivy / image pinning** — Base images are pinned by digest and scanned; CVEs above threshold block promotion of a new runner image.

**three-layer defense** — TFactory composes three layers: (1) OS/Docker sandbox isolation, (2) filesystem permissions (read-only mount + scoped `/scratch`), (3) the dynamic command allowlist derived from project analysis. A generated test must clear all three.

**signed commits** — When git-write is opted in, the Triager's commit is signed (`-s` sign-off, `-S` GPG) so the provenance of machine-authored test commits is verifiable in history.

**capability drop + no-new-privileges** — Runners drop all Linux capabilities and set `no-new-privileges`, so even a setuid binary inside the container can't escalate. Combined with `--read-only` rootfs, the blast radius of a malicious test is a tmpfs `/scratch`.

**out-of-scope: app SAST/DAST** — The sandbox *isolates* untrusted test code; it does not scan the application-under-test for vulnerabilities. Application security scanning is delegated to dedicated pipelines, by design.

---

## Common tasks

### Verify the per-lane network policy
Unit lanes must be air-gapped; networked lanes get bridge + egress control.
```yaml
# conceptual runner policy (enforced by docker_runner)
lanes:
  unit:        { network: none }                 # air-gapped
  api:         { network: bridge }
  browser:     { network: bridge }
  integration: { network: bridge }
egress:
  enabled: false        # default deny: reach the target, not the internet
  allow: []             # add only the hosts you must (IdP, target)
```

### The hardened run invocation (shape)
```bash
docker run --rm \
  --network=none \                       # unit lane; bridge for browser/api
  --read-only \                          # rootfs read-only
  -v "$REPO:/code:ro" \                  # code-under-test read-only
  --tmpfs /scratch:rw,size=512m \        # only writable surface
  --memory=2g --cpus=2 --pids-limit=512 \
  --cap-drop=ALL --security-opt=no-new-privileges \
  # NOTE: NO -v /var/run/docker.sock — never mounted
  tfactory/runner@sha256:<pinned-digest> pytest -q
```

### Keep side-effects dry-run (default) — opt in explicitly
```bash
# Default: nothing is pushed or posted. To opt in (deliberately):
export TFACTORY_TRIAGER_GIT_WRITE=1     # commit tests to the feature branch
export TFACTORY_TRIAGER_PR_COMMENT=1    # gh pr comment the triage report
export TFACTORY_HANDBACK_SEND=1         # POST a correction to AIFactory
# Leave all unset for a fully read-only run.
```

### Scan the runner base image (supply-chain)
```bash
trivy image --severity HIGH,CRITICAL --exit-code 1 \
  tfactory/runner@sha256:<pinned-digest>
# non-zero exit => block promoting this runner image
```

### Sign the commit the Triager writes (when git-write is on)
```bash
git config commit.gpgsign true
git commit -s -S -m "test: add TFactory-generated tests for <spec>"
```

### Bound a runaway test
```bash
# wall-clock timeout in addition to the docker resource caps
timeout 300 docker run --rm --network=none --memory=2g --pids-limit=512 ... 
```

### Drop capabilities and block privilege escalation
A test never needs root caps. Drop them all and forbid setuid escalation.
```bash
docker run --rm \
  --cap-drop=ALL \                       # no Linux capabilities
  --security-opt=no-new-privileges \     # setuid binaries can't escalate
  --read-only -v "$REPO:/code:ro" \
  --tmpfs /scratch:rw \
  tfactory/runner@sha256:<digest> pytest -q
```

### Verify the run was read-only after the fact
A passing test that wrote to source is a sandbox breach; assert the source is untouched.
```bash
git -C "$REPO" diff --quiet && echo "source unchanged: OK" \
  || echo "BREACH: generated test mutated source"
```

### Allow exactly the egress a networked lane needs (default deny)
A browser lane hitting an off-box target + SSO IdP needs precisely those hosts — nothing else.
```yaml
egress:
  enabled: true
  allow:
    - "staging.example.com"      # the target
    - "idp.example.com"          # the IdP for SSO
  # everything else (npm registry, telemetry, attacker C2) stays blocked
```

### Scrub run artifacts before they become evidence
`/scratch` outputs (junit, coverage, Playwright traces) can capture tokens from the DOM or headers. Strip them before upload.
```bash
# redact obvious secret patterns from artifacts before publishing
grep -rIl --include='*.xml' --include='*.json' . /scratch \
  | xargs -r sed -i -E 's/(token|password|secret)=[^&" ]+/\1=REDACTED/g'
```

### Confirm the socket is genuinely absent
A misconfigured base compose file can re-introduce the socket. Assert it's gone.
```bash
docker run --rm tfactory/runner@sha256:<digest> \
  sh -c 'test ! -S /var/run/docker.sock && echo "no docker.sock: OK"'
```

---

## Gotchas
1. **A "unit" test that needs the network** — On `--network=none` it fails with name-resolution/connection errors. That's the sandbox doing its job: the test is mis-laned, not broken. Move it to `api`/`integration`.
2. **Test writes to the repo and "passes" locally but fails in sandbox** — The mount is `:ro`. Any write to source must go to `/scratch`. Fixtures that write next to the test will fail under the read-only mount.
3. **Secret readable inside the container** — It's *meant* to be reachable as env for the run, but it's wiped at teardown and never on the mount. If a secret shows up in a committed artifact, that's a leak — scrub `/scratch` outputs.
4. **Assuming bridge = open internet** — `--network=bridge` still respects the egress gate. Default deny means the target works but `curl evil.com` doesn't. Don't rely on outbound unless you allowed it.
5. **Wanting docker-in-docker** — Generated tests cannot get the socket. If a test "needs to spin up a container", redesign it (use the declared target) — the socket will never be mounted.
6. **`:latest` base image drift** — An unpinned runner image silently changes, breaking reproducibility and dodging CVE scans. Pin by digest.
7. **Forgetting side-effects are OFF by default** — Operators sometimes expect tests to auto-commit. They don't, by policy. Nothing pushes until the env opt-in is set.

8. **Artifacts leak secrets** — A Playwright trace or junit message can embed a token captured from a header or the DOM. The mount being read-only doesn't help here — the leak is in the *output*. Scrub `/scratch` before upload.

9. **Egress allowlist forgotten after SSO works locally** — Login worked locally because your machine had open network; in the sandbox default-deny, the IdP is blocked. Add the IdP host to `egress.allow`.

---

## Anti-patterns / common mistakes
| Mistake | Why it's wrong | What to do instead |
|---|---|---|
| Granting unit lane `--network=bridge` | Untrusted code gets needless network; widens attack surface | Keep unit `--network=none`; only networked lanes get bridge |
| Mounting the repo read-write | A generated test can mutate/corrupt source | Mount `:ro`; write only to `/scratch` |
| Mounting `/var/run/docker.sock` | Full host-daemon takeover / sandbox escape | Never mount it; redesign tests that "need" containers |
| Baking secrets into the runner image | Persists secrets; leaks via image layers | Inject ephemeral env via broker; wipe at teardown |
| No resource limits | One runaway test starves/kills the host | `--memory`/`--cpus`/`--pids-limit` + wall-clock `timeout` |
| Auto-pushing generated tests | Violates no-automatic-pushes policy; surprises operators | Dry-run by default; require explicit `TFACTORY_TRIAGER_GIT_WRITE=1` |
| Running on `:latest` unscanned base image | Unreproducible + unscanned CVEs ship to runners | Pin by digest; gate on Trivy HIGH/CRITICAL |
| Treating the sandbox as app SAST/DAST | Out of scope; sandbox isolates, it doesn't scan app code | Delegate SAST/DAST to dedicated pipelines |
