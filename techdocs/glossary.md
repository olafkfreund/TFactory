# Glossary

| Term | Meaning |
|------|---------|
| **Acceptance criteria (AC)** | The intent a feature must satisfy. TFactory generates tests *to the AC*, not from code. |
| **Lane** | A test modality: `unit · browser · api · integration · mutation`. The Planner tags each subtask with one. |
| **Subtask** | One unit of test work in `test_plan.json`, carrying `(language, framework, lane, target_name, intent)`. |
| **Verdict** | The Evaluator's per-test decision: `accept`, `reject`, or `flag`. |
| **5-signal pipeline** | coverage delta · 3× stability · mutate-and-check · flake-lint promotion · LLM semantic relevance. |
| **Mutate-and-check** | Mutating one assertion to confirm the test *fails* (KILLED) rather than passing blindly (SURVIVED). |
| **Triage report** | The ranked, explained, deduped output (`findings/triage_report.{md,json}`) ready for the PR. |
| **Auto-fire chain** | Each stage calls `schedule_<next>()` on success; gated by `TFACTORY_AUTO_*` env flags. |
| **Dry-run** | Git commit / PR comment / handback computed but not sent unless explicitly opted in. |
| **Completion-event envelope** | Normalized v1 terminal event the Triager emits (webhook / sentinel). |
| **Handback** | TFactory → AIFactory correction artifact when tests find problems (epic #182). |
| **PARR spine** | The Factory platform: AIFactory · TFactory · PFactory · CFactory, joined by `correlation_id` (GitHub issue #). |
| **Framework registry** | Maps a subtask's `(language, framework, lane)` to a runtime image + prompt context block. |
| **runner_fn** | The mockable seam wrapping the Docker executor so tests pass canned exit codes. |
| **Workspace** | Per-task state at `~/.tfactory/workspaces/<project>/specs/<spec>/`. |
| **BYO-LLM** | Bring-your-own-LLM; `byo_llm.py` classifies egress posture (LOCAL / SELF_HOSTED / MANAGED_CLOUD). |
| **CSPM** | Cloud Security Posture Management — the read-only AWS/GCP/Azure assessment flow (epic #133). |
| **MCP** | Model Context Protocol — the JSON-RPC tool surface agents use to drive TFactory. |
