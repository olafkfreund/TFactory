# Use TFactory without AIFactory (generic acceptance-criteria sources)

> **TFactory no longer requires AIFactory.** Point it at any acceptance-criteria
> source — plain **markdown**, a **Gherkin `.feature`**, or **EARS**-notation
> requirements — and it normalises them into the same canonical spec the
> Planner already consumes, then runs the full Planner → Gen → Executor →
> Evaluator → Triager pipeline.

This is the standalone-product unlock from the product decision log
(`.agent-os/product/decisions.md`, DEC-001: *standalone product, AIFactory as
the wedge*).

## How it works

The Planner reads `context/aifactory_spec.md` — a markdown spec with `AC#N:`
acceptance criteria. `spec_sources.py` turns any source into that file:

```
markdown / .feature / EARS  ──ingest──►  NormalizedSpec  ──write──►  context/aifactory_spec.md  ──►  Planner
```

## CLI

```bash
cd apps/backend

# Print the normalised spec (auto-detects format from content/extension)
python spec_sources.py login.feature

# Drop it straight into a task's context dir → ready for the Planner
python spec_sources.py reqs.md --context ~/.tfactory/workspaces/<proj>/specs/<spec>/context

# Force a format / set a title
python spec_sources.py requirements.txt --format ears --title "Auth service"
```

## Supported sources

### Markdown
Bullets/numbered items under an **Acceptance Criteria** / **Acceptance** /
**Requirements** heading, or inline `AC#N: …` lines anywhere:

```markdown
# Login feature
## Acceptance Criteria
- User can log in with valid credentials
- Login rejects an expired token
```

### Gherkin (`.feature`)
One acceptance criterion per `Scenario` (name + joined Given/When/Then steps):

```gherkin
Feature: User login
  Scenario: valid credentials
    Given a registered user
    When they submit valid credentials
    Then a session is created
```

### EARS notation
Each requirement line containing **shall** (ubiquitous / event / state /
optional / unwanted-behaviour templates):

```
The system shall reject expired tokens.
When a user submits valid credentials, the system shall create a session.
While offline, the app shall queue requests.
```

## Programmatic API

```python
from spec_sources import ingest_file, write_spec_markdown

spec = ingest_file("login.feature")        # auto-detect
print(spec.title, len(spec.criteria))
write_spec_markdown(spec, context_dir)     # → context/aifactory_spec.md
```

`ingest(text, fmt=None, filename=None, title=None)` is the text entry point;
`detect_format(text, filename=...)` exposes detection on its own.

## Run TFactory on a spec without AIFactory (WS2)

The ingestion above is wired into two first-class entry points that create a
TFactory task directly from a spec — no AIFactory branch required. Both write
the canonical `context/aifactory_spec.md` and a **target-mode** `source.json`
(no branch/diff; the system-under-test is named by `target_paths`), then start
the pipeline:

- **MCP tool** `task_create_from_spec` — `{project_id, spec_id, spec_text,
  format?, target_paths?, confirm}`. Preview unless `confirm=true`.
- **HTTP** `POST /api/specs/ingest` — same fields as a JSON body; the portal's
  "New test from spec" upload calls this.

```bash
curl -H "Authorization: Bearer acw_…" -H "Content-Type: application/json" \
  -d '{"project_id":"demo","spec_id":"login-1","spec_text":"Feature: Login\n  Scenario: ok\n    Then a session is created","target_paths":["src/auth.py"]}' \
  https://your-tfactory-host/api/specs/ingest
```

Because a spec-ingest task has no PR `sha`/`repo`, the WS1 PR quality gate
(`guides/pr-gate.md`) automatically skips for these runs.
