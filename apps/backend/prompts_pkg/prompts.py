"""
Prompt Loading Utilities
========================

Functions for loading agent prompts from markdown files.
Supports dynamic prompt assembly based on project type for context optimization.
Supports Quick Mode for simplified prompts (~70% fewer tokens).
"""

import json
import os
import re
from pathlib import Path

from .project_context import (
    detect_project_capabilities,
    get_mcp_tools_for_project,
    load_project_index,
)

# Directory containing prompt files
# prompts/ is a sibling directory of prompts_pkg/, so go up one level first
PROMPTS_DIR = Path(__file__).parent.parent / "prompts"


def get_planner_prompt(spec_dir: Path) -> str:
    """
    Load the planner agent prompt with spec path injected.
    The planner creates subtask-based implementation plans.

    Args:
        spec_dir: Directory containing the spec.md file

    Returns:
        The planner prompt content with spec path
    """
    # Quick Mode: Use simplified prompt (~70% fewer tokens)
    if os.environ.get("QUICK_MODE") == "true":
        quick_prompt_file = PROMPTS_DIR / "planner_quick.md"
        if quick_prompt_file.exists():
            prompt_file = quick_prompt_file
        else:
            prompt_file = PROMPTS_DIR / "planner.md"
    else:
        prompt_file = PROMPTS_DIR / "planner.md"

    if not prompt_file.exists():
        raise FileNotFoundError(
            f"Planner prompt not found at {prompt_file}\n"
            "Make sure the tfactory/prompts/planner.md file exists."
        )

    prompt = prompt_file.read_text()

    # Inject spec directory information at the beginning
    spec_context = f"""## SPEC LOCATION

Your spec file is located at: `{spec_dir}/spec.md`

🚨 CRITICAL FILE CREATION INSTRUCTIONS 🚨

You MUST use the Write tool to create these files in the spec directory:
- `{spec_dir}/test_plan.json` - Subtask-based implementation plan (USE WRITE TOOL!)
- `{spec_dir}/build-progress.txt` - Progress notes (USE WRITE TOOL!)
- `{spec_dir}/init.sh` - Environment setup script (USE WRITE TOOL!)

DO NOT just describe what these files should contain. You MUST actually call the Write tool
with the file path and complete content to create them.

The project root is the parent of tfactory/. Implement code in the project root, not in the spec directory.

---

"""
    return spec_context + prompt


def get_coding_prompt(spec_dir: Path) -> str:
    """
    Load the coding agent prompt with spec path injected.

    Args:
        spec_dir: Directory containing the spec.md and test_plan.json

    Returns:
        The coding agent prompt content with spec path
    """
    # Quick Mode: Use simplified prompt (~70% fewer tokens)
    if os.environ.get("QUICK_MODE") == "true":
        quick_prompt_file = PROMPTS_DIR / "coder_quick.md"
        if quick_prompt_file.exists():
            prompt_file = quick_prompt_file
        else:
            prompt_file = PROMPTS_DIR / "coder.md"
    else:
        prompt_file = PROMPTS_DIR / "coder.md"

    if not prompt_file.exists():
        raise FileNotFoundError(
            f"Coding prompt not found at {prompt_file}\n"
            "Make sure the tfactory/prompts/coder.md file exists."
        )

    prompt = prompt_file.read_text()

    spec_context = f"""## SPEC LOCATION

Your spec and progress files are located at:
- Spec: `{spec_dir}/spec.md`
- Implementation plan: `{spec_dir}/test_plan.json`
- Progress notes: `{spec_dir}/build-progress.txt`
- Recovery context: `{spec_dir}/memory/attempt_history.json`

The project root is the parent of tfactory/. All code goes in the project root, not in the spec directory.

---

"""

    # Check for recovery context (stuck subtasks, retry hints)
    recovery_context = _get_recovery_context(spec_dir)
    if recovery_context:
        spec_context += recovery_context

    # Check for human input file
    human_input_file = spec_dir / "HUMAN_INPUT.md"
    if human_input_file.exists():
        human_input = human_input_file.read_text().strip()
        if human_input:
            spec_context += f"""## HUMAN INPUT (READ THIS FIRST!)

The human has left you instructions. READ AND FOLLOW THESE CAREFULLY:

{human_input}

After addressing this input, you may delete or clear the HUMAN_INPUT.md file.

---

"""

    return spec_context + prompt


def _get_recovery_context(spec_dir: Path) -> str:
    """
    Get recovery context if there are failed attempts or stuck subtasks.

    Args:
        spec_dir: Spec directory containing memory/

    Returns:
        Recovery context string or empty string
    """
    import json

    attempt_history_file = spec_dir / "memory" / "attempt_history.json"

    if not attempt_history_file.exists():
        return ""

    try:
        with open(attempt_history_file) as f:
            history = json.load(f)

        # Check for stuck subtasks
        stuck_subtasks = history.get("stuck_subtasks", [])
        if stuck_subtasks:
            context = """## ⚠️ RECOVERY ALERT - STUCK SUBTASKS DETECTED

Some subtasks have been attempted multiple times without success. These subtasks need:
- A COMPLETELY DIFFERENT approach
- Possibly simpler implementation
- Or escalation to human if infeasible

Stuck subtasks:
"""
            for stuck in stuck_subtasks:
                context += f"- {stuck['subtask_id']}: {stuck['reason']} ({stuck['attempt_count']} attempts)\n"

            context += "\nBefore working on any subtask, check memory/attempt_history.json for previous attempts!\n\n---\n\n"
            return context

        # Check for subtasks with multiple attempts
        subtasks_with_retries = []
        for subtask_id, subtask_data in history.get("subtasks", {}).items():
            attempts = subtask_data.get("attempts", [])
            if len(attempts) > 1 and subtask_data.get("status") != "completed":
                subtasks_with_retries.append((subtask_id, len(attempts)))

        if subtasks_with_retries:
            context = """## ⚠️ RECOVERY CONTEXT - RETRY AWARENESS

Some subtasks have been attempted before. When working on these:
1. READ memory/attempt_history.json for the specific subtask
2. See what approaches were tried
3. Use a DIFFERENT approach

Subtasks with previous attempts:
"""
            for subtask_id, attempt_count in subtasks_with_retries:
                context += f"- {subtask_id}: {attempt_count} attempts\n"

            context += "\n---\n\n"
            return context

        return ""

    except (OSError, json.JSONDecodeError):
        return ""


def get_followup_planner_prompt(spec_dir: Path) -> str:
    """
    Load the follow-up planner agent prompt with spec path and key files injected.
    The follow-up planner adds new subtasks to an existing completed implementation plan.

    Args:
        spec_dir: Directory containing the completed spec and test_plan.json

    Returns:
        The follow-up planner prompt content with paths injected
    """
    prompt_file = PROMPTS_DIR / "followup_planner.md"

    if not prompt_file.exists():
        raise FileNotFoundError(
            f"Follow-up planner prompt not found at {prompt_file}\n"
            "Make sure the tfactory/prompts/followup_planner.md file exists."
        )

    prompt = prompt_file.read_text()

    # Inject spec directory information at the beginning
    spec_context = f"""## SPEC LOCATION (FOLLOW-UP MODE)

You are adding follow-up work to a **completed** spec.

**Key files in this spec directory:**
- Spec: `{spec_dir}/spec.md`
- Follow-up request: `{spec_dir}/FOLLOWUP_REQUEST.md` (READ THIS FIRST!)
- Implementation plan: `{spec_dir}/test_plan.json` (APPEND to this, don't replace)
- Progress notes: `{spec_dir}/build-progress.txt`
- Context: `{spec_dir}/context.json`
- Memory: `{spec_dir}/memory/`

**Important paths:**
- Spec directory: `{spec_dir}`
- Project root: Parent of tfactory/ (where code should be implemented)

**Your task:**
1. Read `{spec_dir}/FOLLOWUP_REQUEST.md` to understand what to add
2. Read `{spec_dir}/test_plan.json` to see existing phases/subtasks
3. ADD new phase(s) with pending subtasks to the existing plan
4. PRESERVE all existing subtasks and their statuses

---

"""
    return spec_context + prompt


def is_first_run(spec_dir: Path) -> bool:
    """
    Check if this is the first run (no valid implementation plan with subtasks exists yet).

    The spec runner may create a skeleton test_plan.json with empty phases.
    This function checks for actual phases with subtasks, not just file existence.

    Args:
        spec_dir: Directory containing spec files

    Returns:
        True if test_plan.json doesn't exist or has no subtasks
    """
    plan_file = spec_dir / "test_plan.json"

    if not plan_file.exists():
        return True

    try:
        with open(plan_file) as f:
            plan = json.load(f)

        # Check if there are any phases with subtasks
        phases = plan.get("phases", [])
        if not phases:
            return True

        # Check if any phase has subtasks
        total_subtasks = sum(len(phase.get("subtasks", [])) for phase in phases)
        return total_subtasks == 0
    except (OSError, json.JSONDecodeError):
        # If we can't read the file, treat as first run
        return True


def _load_prompt_file(filename: str) -> str:
    """
    Load a prompt file from the prompts directory.

    Args:
        filename: Relative path to prompt file (e.g., "qa_reviewer.md" or "mcp_tools/playwright_browser.md")

    Returns:
        Content of the prompt file

    Raises:
        FileNotFoundError: If prompt file doesn't exist
    """
    prompt_file = PROMPTS_DIR / filename
    if not prompt_file.exists():
        raise FileNotFoundError(f"Prompt file not found: {prompt_file}")
    return prompt_file.read_text()


def get_qa_reviewer_prompt(spec_dir: Path, project_dir: Path) -> str:
    """
    Load the QA reviewer prompt with project-specific MCP tools dynamically injected.

    This function:
    1. Loads the base QA reviewer prompt
    2. Detects project capabilities from project_index.json
    3. Injects only relevant MCP tool documentation (Electron, Puppeteer, DB, API)

    This saves context window by excluding irrelevant tool docs.
    For example, a CLI Python project won't get Electron validation docs.

    Args:
        spec_dir: Directory containing the spec files
        project_dir: Root directory of the project

    Returns:
        The QA reviewer prompt with project-specific tools injected
    """
    # Quick Mode: Use simplified prompt (~70% fewer tokens)
    # Note: Quick mode uses simplified prompt without MCP tool injection
    if os.environ.get("QUICK_MODE") == "true":
        quick_prompt_file = PROMPTS_DIR / "qa_reviewer_quick.md"
        if quick_prompt_file.exists():
            base_prompt = quick_prompt_file.read_text()
            # Add basic spec context and return (skip MCP tool injection for speed)
            spec_context = f"""## SPEC LOCATION

Your spec and progress files are located at:
- Spec: `{spec_dir}/spec.md`
- Implementation plan: `{spec_dir}/test_plan.json`
- QA report output: `{spec_dir}/qa_report.md`

The project root is: `{project_dir}`

---

"""
            return spec_context + base_prompt

    # Load base QA reviewer prompt (full mode with MCP tools)
    base_prompt = _load_prompt_file("qa_reviewer.md")

    # Load project index and detect capabilities
    project_index = load_project_index(project_dir)
    capabilities = detect_project_capabilities(project_index)

    # Get list of MCP tool doc files to include
    mcp_tool_files = get_mcp_tools_for_project(capabilities)

    # Load and assemble MCP tool sections
    mcp_sections = []
    for tool_file in mcp_tool_files:
        try:
            section = _load_prompt_file(tool_file)
            mcp_sections.append(section)
        except FileNotFoundError:
            # Skip missing files gracefully
            pass

    # Inject spec context at the beginning
    spec_context = f"""## SPEC LOCATION

Your spec and progress files are located at:
- Spec: `{spec_dir}/spec.md`
- Implementation plan: `{spec_dir}/test_plan.json`
- Progress notes: `{spec_dir}/build-progress.txt`
- QA report output: `{spec_dir}/qa_report.md`
- Fix request output: `{spec_dir}/QA_FIX_REQUEST.md`

The project root is: `{project_dir}`

---

## PROJECT CAPABILITIES DETECTED

"""

    # Add capability summary for transparency
    active_caps = [k for k, v in capabilities.items() if v]
    if active_caps:
        spec_context += (
            "Based on project analysis, the following capabilities were detected:\n"
        )
        for cap in active_caps:
            cap_name = (
                cap.replace("is_", "").replace("has_", "").replace("_", " ").title()
            )
            spec_context += f"- {cap_name}\n"
        spec_context += "\nRelevant validation tools have been included below.\n\n"
    else:
        spec_context += (
            "No special project capabilities detected. Using standard validation.\n\n"
        )

    spec_context += "---\n\n"

    # Find injection point in base prompt (after PHASE 4, before PHASE 5)
    injection_marker = (
        "<!-- PROJECT-SPECIFIC VALIDATION TOOLS WILL BE INJECTED HERE -->"
    )

    if mcp_sections and injection_marker in base_prompt:
        # Replace marker with actual MCP tool sections
        mcp_content = "\n\n---\n\n## PROJECT-SPECIFIC VALIDATION TOOLS\n\n"
        mcp_content += "The following validation tools are available based on your project type:\n\n"
        mcp_content += "\n\n---\n\n".join(mcp_sections)
        mcp_content += "\n\n---\n"

        # Replace the multi-line marker comment block
        marker_pattern = r"<!-- PROJECT-SPECIFIC VALIDATION TOOLS WILL BE INJECTED HERE -->.*?<!-- - API validation \(for projects with API endpoints\) -->"
        base_prompt = re.sub(marker_pattern, mcp_content, base_prompt, flags=re.DOTALL)
    elif mcp_sections:
        # Fallback: append at the end if marker not found
        base_prompt += "\n\n---\n\n## PROJECT-SPECIFIC VALIDATION TOOLS\n\n"
        base_prompt += "\n\n---\n\n".join(mcp_sections)

    return spec_context + base_prompt


def get_qa_fixer_prompt(spec_dir: Path, project_dir: Path) -> str:
    """
    Load the QA fixer prompt with spec paths injected.

    Args:
        spec_dir: Directory containing the spec files
        project_dir: Root directory of the project

    Returns:
        The QA fixer prompt content with paths injected
    """
    base_prompt = _load_prompt_file("qa_fixer.md")

    spec_context = f"""## SPEC LOCATION

Your spec and progress files are located at:
- Spec: `{spec_dir}/spec.md`
- Implementation plan: `{spec_dir}/test_plan.json`
- QA fix request: `{spec_dir}/QA_FIX_REQUEST.md` (READ THIS FIRST!)
- QA report: `{spec_dir}/qa_report.md`

The project root is: `{project_dir}`

---

"""
    return spec_context + base_prompt


# ---------------------------------------------------------------------------
# TFactory Planner prompt helpers — Task 5 (#6), commit 3 of 6.
#
# These are distinct from the inherited get_planner_prompt /
# get_followup_planner_prompt helpers above, which target AIFactory's
# coder-pipeline planner. The TFactory variants load the new
# test-oriented prompts in apps/backend/prompts/planner.md +
# planner_replan.md, then prepend a SPEC CONTEXT block with the
# concrete spec_dir + project_dir paths.
# ---------------------------------------------------------------------------


def _build_framework_registry_block() -> str:
    """Return a FRAMEWORK REGISTRY summary block for the planner prompt.

    Deferred import of framework_registry avoids circular imports at
    module level (the same pattern Task 4 uses for tfactory_yml).
    If the registry is unavailable (e.g. frameworks/ dir absent) the
    block is replaced by a minimal placeholder so the helper stays
    non-fatal — the validator in planner.py degrades gracefully too.

    Returns:
        Markdown string starting with '## FRAMEWORK REGISTRY'.
    """
    try:
        from framework_registry import load_registry  # deferred: not on hot path

        registry = load_registry()
        if not registry:
            return "## FRAMEWORK REGISTRY\n(no frameworks registered)\n"
        lines = ["## FRAMEWORK REGISTRY"]
        for name, desc in sorted(registry.items()):
            lane_vals = ", ".join(
                ln.value if hasattr(ln, "value") else str(ln) for ln in desc.lanes
            )
            lines.append(
                f"- {name}: language={desc.language},"
                f" lanes=[{lane_vals}],"
                f" image={desc.runtime.image}"
            )
        return "\n".join(lines) + "\n"
    except Exception:  # noqa: BLE001
        return (
            "## FRAMEWORK REGISTRY\n"
            "(registry unavailable — use pytest for Python, jest for TypeScript)\n"
        )


# Acceptance-criteria *command* tokens → target language. This is the PRIMARY
# language signal (#443): a Go spec says "`go test ./...` passes", a Python one
# says "pytest", etc. Ordered by priority; first hit wins. Manifest files only
# corroborate (a repo can carry go.mod AND pyproject.toml — e.g. the polyglot
# benchmark repo — so a manifest scan alone is ambiguous).
_AC_COMMAND_LANGUAGE: tuple[tuple[str, str], ...] = (
    ("go test", "go"),
    ("go build", "go"),
    ("cargo test", "rust"),
    ("cargo build", "rust"),
    ("pytest", "python"),
    ("npm test", "typescript"),
    ("jest", "typescript"),
    ("vitest", "typescript"),
)


def _unit_framework_for_language(registry: dict, language: str) -> str | None:
    """Return the registered unit-lane framework for ``language`` (or ``None``)."""
    for name, desc in sorted(registry.items()):
        if desc.language != language:
            continue
        if any(
            (ln.value if hasattr(ln, "value") else str(ln)) == "unit"
            for ln in desc.lanes
        ):
            return name
    return None


def _build_detected_language_block(spec_dir: Path, project_dir: Path) -> str:
    """Pin the target language deterministically so the Planner can't default to pytest.

    The framework-picking algorithm in ``planner.md`` is diff-/stack-sniff based and
    was Python/TypeScript-biased: a Go (or other) project whose changed files the
    agent could not classify fell through to the ``(python, pytest)`` default — it
    emitted ``.py`` tests for a ``.go`` target and left ``language: None`` (#443).

    This block reads two deterministic signals and states the ``(language,
    framework)`` the agent MUST use for unit subtasks:
      1. the spec's acceptance-criteria *commands* (PRIMARY — ``go test`` → Go);
      2. the project's manifest files (CORROBORATING — ``go.mod`` → Go), derived
         from each registry descriptor's ``manifest_signals``.

    Best-effort: when no signal is found it tells the agent to detect the language
    via Glob rather than guessing. Never raises — degrades to guidance text.
    """
    header = "## DETECTED PROJECT LANGUAGE"

    try:
        from framework_registry import load_registry  # deferred: not on hot path

        registry = load_registry()
    except Exception:  # noqa: BLE001 — never break planning on a registry read
        registry = {}

    # (1) PRIMARY: scan the frozen spec text for acceptance-criteria commands.
    spec_text = ""
    for rel in ("context/aifactory_spec.md", "context/source.json"):
        try:
            spec_text += (spec_dir / rel).read_text(encoding="utf-8").lower()
        except Exception:  # noqa: BLE001 — missing/unreadable context is fine
            pass
    ac_language: str | None = None
    for token, language in _AC_COMMAND_LANGUAGE:
        if token in spec_text:
            ac_language = language
            break

    # (2) CORROBORATING: which registered manifests actually exist on disk.
    manifest_to_lang: dict[str, str] = {}
    for desc in registry.values():
        for sig in desc.manifest_signals:
            manifest_to_lang.setdefault(sig.split(":", 1)[0], desc.language)
    present_manifests = sorted(
        fname for fname in manifest_to_lang if _safe_manifest_exists(project_dir, fname)
    )
    manifest_langs = sorted({manifest_to_lang[f] for f in present_manifests})

    chosen = ac_language or (manifest_langs[0] if len(manifest_langs) == 1 else None)

    if chosen is None:
        if manifest_langs:
            return (
                f"{header}\n"
                f"Multiple language manifests are present "
                f"({', '.join(present_manifests)}) and the acceptance criteria name "
                "no build/test command. Determine the target language from the "
                "changed files / target path before choosing a framework — do NOT "
                "default to pytest for a non-Python target.\n"
            )
        return (
            f"{header}\n"
            "No deterministic language signal (no AC build/test command, no known "
            "manifest). Detect the language via Glob on the project before choosing "
            "a framework; never assume pytest.\n"
        )

    framework = _unit_framework_for_language(registry, chosen)
    signal = (
        f"the acceptance criteria invoke a `{_first_ac_token(spec_text)}` command"
        if ac_language
        else f"the project manifest ({', '.join(present_manifests)}) is {chosen}"
    )
    corroboration = (
        f" (manifest scan saw: {', '.join(present_manifests)})"
        if present_manifests
        else ""
    )
    fw_clause = (
        f" Use `framework: {framework}` for unit subtasks."
        if framework
        else " No unit framework is registered for this language yet; pick the "
        "closest registry entry and note it in `rationale`."
    )
    py_warning = (
        " Do NOT emit pytest / `.py` tests for this target."
        if chosen != "python"
        else ""
    )
    return (
        f"{header}\n"
        f"This is a **{chosen}** project — {signal}{corroboration}. "
        f"Set `language: {chosen}` on every unit subtask.{fw_clause}{py_warning}\n"
    )


def _first_ac_token(spec_text: str) -> str:
    """Return the first AC command token found in ``spec_text`` (for the message)."""
    for token, _ in _AC_COMMAND_LANGUAGE:
        if token in spec_text:
            return token
    return "build/test"


def _safe_manifest_exists(project_dir: Path, fname: str) -> bool:
    """``(project_dir / fname).is_file()`` guarded against odd values."""
    try:
        return (project_dir / fname).is_file()
    except Exception:  # noqa: BLE001
        return False


def _build_tests_catalog_block(spec_dir: Path) -> str:
    """Return a TESTS CATALOG summary block for the planner prompt.

    Reads the frozen ``context/tests_catalog.json`` written by Task 4's
    snapshotter (if present).  We load it directly from the JSON rather
    than calling ``load_catalog(repo_root)`` because the snapshotter
    already wrote a frozen copy scoped to this workspace.

    Returns:
        Markdown string starting with '## TESTS CATALOG'.
    """
    import json as _json

    catalog_path = spec_dir / "context" / "tests_catalog.json"
    if not catalog_path.exists():
        return (
            "## TESTS CATALOG\n"
            "(no catalog at this repo yet — every subtask uses intent: create)\n"
        )
    try:
        from tests_catalog import TestsCatalog  # deferred: not on hot path

        data = _json.loads(catalog_path.read_text())
        catalog = TestsCatalog.from_dict(data)
        if not catalog.tests:
            return (
                "## TESTS CATALOG\n"
                "(catalog present but empty — every subtask uses intent: create)\n"
            )
        lines = [
            "## TESTS CATALOG (existing tests in this repo)",
            "Existing entries (use intent: update if your AC matches an existing covers_acs):",
        ]
        for entry in catalog.tests:
            acs_preview = "; ".join(entry.covers_acs[:2])
            if len(entry.covers_acs) > 2:
                acs_preview += f" (+ {len(entry.covers_acs) - 2} more)"
            locked = " [operator_locked]" if entry.operator_locked else ""
            lines.append(
                f"- {entry.test_id}: framework={entry.framework},"
                f" lane={entry.lane},"
                f" test_file={entry.test_file},"
                f" covers_acs=[{acs_preview!r}]{locked}"
            )
        lines.append(f"(catalog has {len(catalog.tests)} entries total)")
        return "\n".join(lines) + "\n"
    except Exception:  # noqa: BLE001
        return (
            "## TESTS CATALOG\n"
            "(catalog present but could not be parsed — treating as absent)\n"
        )


def get_tfactory_planner_prompt(spec_dir: Path, project_dir: Path) -> str:
    """Assemble the initial-mode Planner prompt for TFactory.

    Loads apps/backend/prompts/planner.md and prepends a SPEC CONTEXT
    block that names the concrete paths the agent will read + write,
    followed by a FRAMEWORK REGISTRY block (Task 5, #21) summarising
    the available frameworks, and a TESTS CATALOG block (Task 5, #21)
    listing existing tests so the agent can set intent: update/skip.

    The template uses `{spec_dir}` / `{project_dir}` placeholders that
    survive to the agent verbatim — the agent uses the Read tool to
    fetch the files, so the placeholders in the prompt body are
    illustrative; the SPEC CONTEXT block we prepend here has the
    real paths.

    Args:
        spec_dir: Absolute path to the TFactory workspace spec dir.
        project_dir: Absolute path to the AIFactory project root.

    Returns:
        Full system prompt ready to hand to run_agent_session.

    Raises:
        FileNotFoundError: if planner.md is missing.
    """
    prompt_file = PROMPTS_DIR / "planner.md"
    if not prompt_file.exists():
        raise FileNotFoundError(
            f"planner.md missing at {prompt_file}. "
            "TFactory's Task 5 (#6) authors this prompt; check the working tree."
        )

    body = prompt_file.read_text()

    # Registry + catalog blocks (Task 5, #21). Deferred imports inside
    # helper functions avoid circular imports at module level.
    registry_block = _build_framework_registry_block()
    # Deterministic language pin (#443) — keep the agent from defaulting a Go (or
    # other non-Python) target to pytest. Sits next to the registry it references.
    language_block = _build_detected_language_block(spec_dir, project_dir)
    catalog_block = _build_tests_catalog_block(spec_dir)
    # RFC-0002 declared test profile (#246) — authoritative over inference.
    profile_block = _build_contract_profile_block(spec_dir)
    # RFC-0012 house testing-standards — follow the team's own test conventions.
    house_block = _build_house_standards_block(spec_dir)

    context = (
        "## SPEC CONTEXT (TFactory Planner — initial mode)\n\n"
        f"Concrete paths for this run:\n\n"
        f"- spec_dir: `{spec_dir}`\n"
        f"- project_dir: `{project_dir}` (read-only via Glob/Grep)\n\n"
        "Files Task 3's snapshotter already populated:\n\n"
        f"- `{spec_dir / 'context' / 'aifactory_spec.md'}`\n"
        f"- `{spec_dir / 'context' / 'aifactory_plan.json'}` (may not exist)\n"
        f"- `{spec_dir / 'context' / 'diff.patch'}` (may not exist if git skipped)\n"
        f"- `{spec_dir / 'context' / 'source.json'}` (snapshot warnings — read first)\n"
        f"- `{spec_dir / 'context' / 'tfactory_yml.json'}` (may not exist)\n"
        f"- `{spec_dir / 'context' / 'tests_catalog.json'}` (may not exist)\n\n"
        f"Emit `test_plan.json` via Write at: `{spec_dir / 'test_plan.json'}`\n\n"
        "---\n\n"
        f"{profile_block}"
        f"{house_block}"
        f"{registry_block}\n"
        f"{language_block}\n"
        f"{catalog_block}\n"
        "---\n\n"
    )
    return context + body


def _build_house_standards_block(spec_dir: Path) -> str:
    """Render the team's house testing-standards block (RFC-0012), or "".

    Surfaces ``epic_context.house_standards`` from the RFC-0002 contract so the
    Planner generates tests the way *this team* tests (its frameworks, test
    layout, golden-path guides) rather than generic defaults. Best-effort:
    degrades to "" when no standards were retrieved; the fail-closed
    ``standards_conformance`` gate catches a retrieved standard that is ignored.
    """
    try:
        from agents.task_contract import read_task_contract

        contract = read_task_contract(spec_dir)
    except Exception:  # noqa: BLE001 — never break planning on a contract read
        contract = None
    if not isinstance(contract, dict):
        return ""
    epic_context = contract.get("epic_context")
    house = (
        epic_context.get("house_standards") if isinstance(epic_context, dict) else None
    )
    if not isinstance(house, dict) or not house.get("available"):
        return ""
    sources = [s for s in house.get("sources", []) if isinstance(s, dict)]
    if not sources:
        return ""

    tools: list[str] = []
    test_layout: list[str] = []
    techdocs: list[str] = []
    lifecycle = None
    for src in sources:
        conv = src.get("conventions")
        if isinstance(conv, dict):
            tools += [str(t) for t in conv.get("code_quality_tools", []) or []]
            layout = conv.get("test_layout")
            if layout:
                test_layout.append(str(layout))
        techdocs += [str(r) for r in src.get("techdocs_refs", []) or []]
        lifecycle = lifecycle or src.get("lifecycle")

    lines = ["## HOUSE TESTING STANDARDS (RFC-0012 — FOLLOW THESE)\n"]
    lines.append(
        "This team has its own standards. Plan tests the way they test — over "
        "generic defaults. The `standards_conformance` gate fails the build when "
        "a retrieved standard is ignored.\n"
    )
    if tools:
        lines.append(f"- **Quality tools to honor:** {', '.join(dict.fromkeys(tools))}")
    if test_layout:
        lines.append(f"- **Test layout:** {', '.join(dict.fromkeys(test_layout))}")
    if lifecycle:
        lines.append(f"- **Component lifecycle:** {lifecycle}")
    if techdocs:
        lines.append("- **Consult these team testing guides (TechDocs):**")
        for ref in dict.fromkeys(techdocs):
            lines.append(f"  - `{ref}`")
    return "\n".join(lines) + "\n\n"


def _build_contract_profile_block(spec_dir: Path) -> str:
    """Render the authoritative DECLARED TEST PROFILE block from an RFC-0002
    contract, or "" when none is present (#246).

    When PFactory has computed the VERIFY profile, the Planner must use the
    declared lanes/frameworks/endpoints rather than inferring them from the
    diff. Inference only fills gaps the contract leaves unspecified.
    """
    try:
        from agents.task_contract import read_tfactory_profile

        profile = read_tfactory_profile(spec_dir)
    except Exception:  # noqa: BLE001 — never break planning on a contract read
        profile = None
    # RFC-0011: a difficulty tier raises the required-lane floor (and forces the
    # equivalence lane on a migration/rewrite). Read it from the full contract so
    # a tier can apply even when there is no explicit tfactory block. Additive:
    # absent tier => no tier lanes, behaviour unchanged.
    tier_lanes: tuple[str, ...] = ()
    try:
        from agents.task_contract import read_task_contract
        from agents.tier_floor import (
            change_mode_from_contract,
            lanes_for,
            tier_from_contract,
        )

        _contract = read_task_contract(spec_dir)
        tier_lanes = lanes_for(
            tier_from_contract(_contract), change_mode_from_contract(_contract)
        )
    except Exception:  # noqa: BLE001 — never break planning on a tier read
        tier_lanes = ()

    if profile is None and not tier_lanes:
        return ""

    lines = ["## DECLARED TEST PROFILE (RFC-0002 — AUTHORITATIVE)\n"]
    lines.append(
        "PFactory computed this VERIFY profile and AIFactory carried it. **Use it "
        "as the source of truth** — generate exactly these lanes with these "
        "frameworks/endpoints. Infer from the diff ONLY for fields left "
        "unspecified below. Do not add lanes the profile omits.\n"
    )

    # Merge the contract-declared lanes with the tier-required floor. The tier
    # ADDS lanes (a higher tier raises rigor); it never removes declared lanes.
    declared = tuple(profile.lanes) if profile is not None else ()
    required = list(declared)
    for lane in tier_lanes:
        if lane not in required:
            required.append(lane)
    profile_lanes = tuple(required)

    if profile_lanes:
        lines.append(f"- **lanes** (generate these): {', '.join(profile_lanes)}")
        if tier_lanes:
            added = [ln for ln in tier_lanes if ln not in declared]
            if added:
                lines.append(
                    "  - NOTE: the difficulty tier (RFC-0011) requires these "
                    f"additional lanes: {', '.join(added)}."
                )
        if profile is not None and "security" in profile.lanes:
            lines.append(
                "  - NOTE: `security` lane is OUT OF SCOPE for TFactory (DEC-002) — "
                "delegate to dedicated security pipelines; do not generate SAST/DAST."
            )
    if profile is None:
        # Tier-only profile (no RFC-0002 tfactory block): the lanes above are the
        # whole story — the per-lane framework/endpoint detail is inferred.
        return "\n".join(lines) + "\n\n---\n\n"
    if profile.frameworks:
        fw = ", ".join(f"{k}={v}" for k, v in profile.frameworks.items())
        lines.append(f"- **frameworks** (lane→framework): {fw}")
    if profile.endpoints:
        ep = ", ".join(f"{k}={v}" for k, v in profile.endpoints.items())
        lines.append(f"- **endpoints**: {ep}")
    if profile.docker_compose:
        lines.append(f"- **docker_compose**: `{profile.docker_compose}`")
    if profile.coverage_target is not None:
        lines.append(f"- **coverage_target**: {profile.coverage_target}")
    if profile.mutation_scope:
        lines.append(f"- **mutation_scope**: {', '.join(profile.mutation_scope)}")
    if profile.ac_to_code_map:
        lines.append(
            f"- **ac_to_code_map** ({len(profile.ac_to_code_map)} acceptance "
            "criteria → code) — target each AC's tests precisely at the listed "
            "files/functions; create one phase per AC:"
        )
        # Render the mapping, capped so a large map can't bloat the prompt.
        _AC_CAP = 20
        for i, (ac_id, targets) in enumerate(profile.ac_to_code_map.items()):
            if i >= _AC_CAP:
                lines.append(
                    f"  - … and {len(profile.ac_to_code_map) - _AC_CAP} more "
                    "(see context/aifactory_plan.json `tfactory.ac_to_code_map`)"
                )
                break
            tlist = ", ".join(targets) if targets else "(no files listed)"
            lines.append(f"  - `{ac_id}` → {tlist}")
    return "\n".join(lines) + "\n\n---\n\n"


def get_tfactory_planner_replan_prompt(spec_dir: Path, project_dir: Path) -> str:
    """Assemble the replan-mode Planner prompt for TFactory.

    Loads apps/backend/prompts/planner_replan.md and prepends a
    REPLAN CONTEXT block naming the spec_dir + project_dir.

    The replan_request.json file (read by the agent) is written by
    Gen-Functional (Task 6) before this helper is called; this
    function does NOT validate its presence — the agent surfaces a
    clear error if the file's missing.

    Args:
        spec_dir: Absolute path to the TFactory workspace spec dir.
        project_dir: Absolute path to the AIFactory project root.

    Returns:
        Full system prompt ready to hand to run_agent_session.

    Raises:
        FileNotFoundError: if planner_replan.md is missing.
    """
    prompt_file = PROMPTS_DIR / "planner_replan.md"
    if not prompt_file.exists():
        raise FileNotFoundError(
            f"planner_replan.md missing at {prompt_file}. "
            "TFactory's Task 5 (#6) authors this prompt; check the working tree."
        )

    body = prompt_file.read_text()
    context = (
        "## REPLAN CONTEXT (TFactory Planner — replan mode)\n\n"
        f"Concrete paths for this run:\n\n"
        f"- spec_dir: `{spec_dir}`\n"
        f"- project_dir: `{project_dir}` (read-only via Glob/Grep)\n\n"
        "Files you'll read:\n\n"
        f"- `{spec_dir / 'context' / 'replan_request.json'}` — **read first**\n"
        f"- `{spec_dir / 'test_plan.json'}` — the existing plan to append to\n"
        f"- `{spec_dir / 'context' / 'aifactory_spec.md'}` — original spec\n"
        f"- `{spec_dir / 'context' / 'diff.patch'}` — code surface (may be absent)\n\n"
        f"Write the updated plan back to: `{spec_dir / 'test_plan.json'}`\n\n"
        "---\n\n"
    )
    # RFC-0011 (#444): on a handback/replan, keep the authoritative DECLARED TEST
    # PROFILE in front of the planner so the difficulty-tier lane floor (and any
    # forced equivalence lane) is preserved across the loop. Empty when no
    # contract/tier is present → replan behaviour unchanged.
    profile_block = _build_contract_profile_block(spec_dir)
    return context + profile_block + body


def get_tfactory_gen_functional_prompt(
    spec_dir: Path,
    project_dir: Path,
    subtask,
    framework_descriptor=None,
) -> str:
    """Assemble the per-subtask Gen-Functional prompt — Task 6 (#22).

    v0.2: accepts an optional ``framework_descriptor`` (a
    ``FrameworkDescriptor`` instance) that triggers the generic prompt
    path.  When ``framework_descriptor`` is ``None`` the v0.1 legacy
    prompt path is used (with a ``DeprecationWarning``).

    **v0.2 prompt assembly order:**

    1. SUBTASK CONTEXT block — concrete paths + subtask fields
    2. FRAMEWORK CONTEXT block — injected from
       ``framework_descriptor.context_block``
    3. Generic prompt body from ``gen_functional.md``

    **v0.1 legacy path (framework_descriptor=None):**

    Loads ``gen_functional-v01-legacy.md`` and prepends the same SUBTASK
    CONTEXT block.  Issues a ``DeprecationWarning`` to guide migration.

    Args:
        spec_dir: TFactory workspace spec dir.
        project_dir: AIFactory project root path (Glob/Grep target).
        subtask: A Subtask dataclass instance from the loaded
            ImplementationPlan.  Duck-typed: also accepts a plain dict
            with the same keys (post-JSON-load shape).
        framework_descriptor: A ``FrameworkDescriptor`` instance from
            ``framework_registry.get_descriptor(subtask.framework)``.
            Pass ``None`` to use the v0.1 legacy prompt (warns).

    Returns:
        Full system prompt ready for ``run_agent_session``.

    Raises:
        FileNotFoundError: if the resolved prompt file is missing.
    """
    import warnings

    # Duck-typed accessor for dataclass-or-dict subtask shapes.
    def _get(obj, key, default=None):
        if isinstance(obj, dict):
            return obj.get(key, default)
        return getattr(obj, key, default)

    target = _get(subtask, "target", "?")
    rationale = _get(subtask, "rationale", "?")
    files_to_create = _get(subtask, "files_to_create", []) or []
    description = _get(subtask, "description", "?")
    subtask_id = _get(subtask, "id", "?")
    language = _get(subtask, "language", None) or "?"
    framework = _get(subtask, "framework", None) or "?"
    intent = _get(subtask, "intent", "create") or "create"

    # Verification carries the pytest/jest/playwright command for the Executor.
    # The planner prompt tells the LLM to emit ``"command"``; the Verification
    # dataclass uses ``run``. Both shapes are accepted here.
    verification = _get(subtask, "verification", None)
    if verification is None:
        verification_cmd = "?"
    elif isinstance(verification, dict):
        verification_cmd = verification.get("command") or verification.get("run") or "?"
    else:
        verification_cmd = (
            getattr(verification, "command", None)
            or getattr(verification, "run", None)
            or "?"
        )

    write_path = files_to_create[0] if files_to_create else "?"

    # Multi-artifact overlays (e.g. Cucumber: .feature + step defs + World) must
    # write EVERY file in files_to_create as one consistent set, not a single
    # file. Triggered by the descriptor flag, or whenever the planner emitted
    # more than one file to create.
    multi_artifact = (
        bool(getattr(framework_descriptor, "multi_artifact", False))
        or len(files_to_create) > 1
    )
    if multi_artifact and files_to_create:
        _files = "\n".join(f"    - `{spec_dir / p}`" for p in files_to_create)
        write_instruction = (
            "- write ALL of these files (one consistent artifact set):\n"
            f"{_files}\n"
            "  The Gherkin step text in the .feature MUST match the step "
            "definitions exactly; every step has exactly one definition."
        )
    else:
        write_instruction = f"- write the file at: `{spec_dir / write_path}`"

    # SUBTASK CONTEXT block (shared by both the v0.1 and v0.2 paths).
    context = (
        "## SUBTASK CONTEXT (TFactory Gen-Functional)\n\n"
        f"Subtask: `{subtask_id}` — {description}\n\n"
        f"- target:            `{target}`\n"
        f"- rationale:         {rationale}\n"
        f"- language:          {language}\n"
        f"- framework:         {framework}\n"
        f"- intent:            {intent}\n"
        f"{write_instruction}\n"
        f"- verification:      `{verification_cmd}`\n\n"
        f"Concrete paths for this run:\n\n"
        f"- spec_dir:    `{spec_dir}`\n"
        f"- project_dir: `{project_dir}` (read-only via Glob/Grep)\n\n"
        "Snapshot files Task 3 populated:\n\n"
        f"- `{spec_dir / 'context' / 'aifactory_spec.md'}`\n"
        f"- `{spec_dir / 'context' / 'diff.patch'}` (may not exist)\n"
        f"- `{spec_dir / 'test_plan.json'}` (the full Planner output)\n\n"
        "---\n\n"
    )

    if framework_descriptor is None:
        # v0.1 legacy path — warn and load the legacy prompt.
        warnings.warn(
            "get_tfactory_gen_functional_prompt: framework_descriptor not provided; "
            "falling back to v0.1 legacy prompt path. "
            "Pass framework_descriptor; v0.1 legacy path will be removed in v0.3.",
            DeprecationWarning,
            stacklevel=2,
        )
        legacy_file = PROMPTS_DIR / "gen_functional-v01-legacy.md"
        if not legacy_file.exists():
            raise FileNotFoundError(
                f"gen_functional-v01-legacy.md missing at {legacy_file}. "
                "The v0.1 legacy prompt must be present for this fallback path."
            )
        body = legacy_file.read_text()
        return context + body

    # v0.2 generic path — inject FRAMEWORK CONTEXT then load the generic body.
    framework_name = getattr(framework_descriptor, "name", str(framework_descriptor))
    context_block_text = getattr(framework_descriptor, "context_block", "")
    framework_context = (
        f"## FRAMEWORK CONTEXT ({framework_name})\n\n{context_block_text}\n\n---\n\n"
    )

    prompt_file = PROMPTS_DIR / "gen_functional.md"
    if not prompt_file.exists():
        raise FileNotFoundError(
            f"gen_functional.md missing at {prompt_file}. "
            "TFactory's Task 6 (#22) commit 1 authors this prompt; check the working tree."
        )
    body = prompt_file.read_text()

    # Final assembly: SUBTASK CONTEXT → FRAMEWORK CONTEXT → generic body
    return context + framework_context + body


# ─── Task 7 (#8) — Evaluator helper ──────────────────────────────────────


def _format_signal_value(obj, *keys, default="?"):
    """Duck-typed accessor: try each key/attr in order; return the first
    non-None value. Lets the helper accept dataclass-shaped OR dict-shaped
    signal inputs (commit 5 will pass dataclasses; future callers may
    pass dicts post-JSON-load)."""
    for key in keys:
        if isinstance(obj, dict):
            val = obj.get(key)
        else:
            val = getattr(obj, key, None)
        if val is not None:
            return val
    return default


def _format_evaluator_per_test_block(bundle) -> str:
    """Render one per-test signal sub-block for the EVALUATOR CONTEXT.

    Duck-typed: ``bundle`` is either an EvaluatorSignals dataclass (commit
    5) or a dict with the same keys. Each field is formatted compactly —
    the LLM reads the prose body for the decision rules; this block is
    just the numeric handles.
    """
    test_id = _format_signal_value(bundle, "test_id")
    test_file = _format_signal_value(bundle, "test_file", "test_file_path")
    target = _format_signal_value(bundle, "target")
    rationale = _format_signal_value(bundle, "rationale")

    coverage = _format_signal_value(bundle, "coverage_delta", "coverage", default=None)
    if coverage is not None:
        # coverage is a CoverageDelta dataclass (or dict); render numeric signals.
        new_lines = _format_signal_value(coverage, "new_lines", default=frozenset())
        new_lines_count = len(new_lines) if hasattr(new_lines, "__len__") else 0
        delta_pct = _format_signal_value(coverage, "delta_pct", default=0.0)
        new_files = _format_signal_value(coverage, "new_files", default=0)
        coverage_line = (
            f"coverage: delta_pct={delta_pct:+.2f}, "
            f"new_lines={new_lines_count}, new_files={new_files}"
        )
    else:
        # coverage_delta is None — either the framework explicitly skips
        # coverage measurement (Browser lane / Playwright, Decision 11) or
        # the coverage XML was absent for this run.
        #
        # In both cases, render "N/A (browser lane)" so the Evaluator LLM
        # does NOT interpret this as "0% coverage" and issue a spurious
        # reject.  The evaluator.md verdict-priority section instructs the
        # LLM to skip the coverage rule when it sees "N/A".
        coverage_line = "coverage: N/A (browser lane)"

    stability = _format_signal_value(bundle, "stability", default=None)
    if stability is not None:
        verdict = _format_signal_value(stability, "verdict", default="?")
        # Verdict is an enum (StabilityVerdict) — `.value` is the string
        verdict_str = getattr(verdict, "value", verdict)
        rerun_count = _format_signal_value(stability, "rerun_count", default="?")
        stability_line = f"stability: {verdict_str} ({rerun_count} runs)"
    else:
        stability_line = "stability: not computed"

    mutation = _format_signal_value(bundle, "mutation", default=None)
    if mutation is not None:
        m_verdict = _format_signal_value(mutation, "verdict", default="?")
        m_verdict_str = getattr(m_verdict, "value", m_verdict)
        m_op = _format_signal_value(mutation, "mutation", default=None)
        op_str = _format_signal_value(m_op, "operator", default="?") if m_op else "?"
        mutation_line = f"mutation: {m_verdict_str} (op={op_str})"
    else:
        mutation_line = "mutation: not computed"

    lint = _format_signal_value(bundle, "lint_promotion", "promotion", default=None)
    if lint is not None:
        summary_fn = getattr(lint, "summary", None)
        if callable(summary_fn):
            lint_summary = summary_fn()
        else:
            lint_summary = (
                f"high={_format_signal_value(lint, 'high_count', default=0)}, "
                f"promoted={_format_signal_value(lint, 'promoted_count', default=0)}, "
                f"medium={_format_signal_value(lint, 'medium_count', default=0)}"
            )
        lint_line = f"lint_promotion: {lint_summary}"
    else:
        lint_line = "lint_promotion: not computed"

    # Cross-run flip-rate (#37): historical flakiness across separate
    # pipeline runs — a chronically flaky test should be flagged/rejected
    # even when this run's 3× stability happened to come up STABLE.
    flaky = _format_signal_value(bundle, "flaky_history", default=None)
    if flaky is not None and _format_signal_value(flaky, "runs", default=0):
        f_class = _format_signal_value(flaky, "classification", default="?")
        f_class_str = getattr(f_class, "value", f_class)
        f_rate = _format_signal_value(flaky, "flip_rate", default=0.0)
        f_runs = _format_signal_value(flaky, "runs", default=0)
        flaky_line = (
            f"flaky_history: {f_class_str} (flip_rate={f_rate:.2f} over {f_runs} runs)"
        )
    else:
        flaky_line = "flaky_history: no prior runs"

    # CI parity (#302): env-parity (creds blanked + UTC + isolation, matching
    # CI) plus a static "real-imports" check — a test that only mocks out the
    # subject module rather than importing it is grading a fake.
    ci_parity = _format_signal_value(bundle, "ci_parity", default=None)
    if ci_parity is not None:
        status_fn = getattr(ci_parity, "status", None)
        status_str = (
            status_fn
            if isinstance(status_fn, str)
            else (status_fn() if callable(status_fn) else "?")
        )
        ri = _format_signal_value(ci_parity, "real_imports", default="?")
        ri_str = getattr(ri, "value", ri)
        reason = _format_signal_value(ci_parity, "reason", default="")
        ci_parity_line = f"ci_parity: {status_str} (real_imports={ri_str})"
        if reason:
            ci_parity_line += f" — {reason}"
    else:
        ci_parity_line = "ci_parity: not computed"

    return (
        f"### Test `{test_id}`\n\n"
        f"- file: `{test_file}`\n"
        f"- target: `{target}`\n"
        f"- rationale: {rationale}\n"
        f"- {coverage_line}\n"
        f"- {stability_line}\n"
        f"- {mutation_line}\n"
        f"- {lint_line}\n"
        f"- {flaky_line}\n"
        f"- {ci_parity_line}\n"
    )


def get_tfactory_evaluator_prompt(
    spec_dir: Path,
    project_dir: Path,
    signal_bundles,
) -> str:
    """Assemble the Evaluator system prompt for TFactory (Task 7 / #8).

    Loads ``apps/backend/prompts/evaluator.md`` and prepends an
    EVALUATOR CONTEXT block that names the concrete paths AND emits
    one sub-block per generated test with the four pre-computed
    numeric signals (coverage / stability / mutation / lint_promotion).
    The fifth signal — semantic relevance — is the LLM's call.

    Args:
        spec_dir: TFactory workspace spec dir.
        project_dir: AIFactory project root (read-only for the agent).
        signal_bundles: Iterable of bundles (dataclass OR dict) carrying
            per-test signals. Each bundle exposes (as attribute OR key):
              - ``test_id``, ``test_file`` / ``test_file_path``
              - ``target``, ``rationale``
              - ``coverage_delta`` (or ``coverage``) — a CoverageDelta
              - ``stability`` — a StabilityResult
              - ``mutation`` — a MutationResult
              - ``lint_promotion`` (or ``promotion``) — a PromotionResult

            Any missing field is rendered as "?" or "not computed".

    Returns:
        Full system prompt ready to hand to ``run_agent_session``.

    Raises:
        FileNotFoundError: if evaluator.md is missing.
    """
    prompt_file = PROMPTS_DIR / "evaluator.md"
    if not prompt_file.exists():
        raise FileNotFoundError(
            f"evaluator.md missing at {prompt_file}. "
            "TFactory's Task 7 (#8) commit 4 authors this prompt; check the working tree."
        )

    body = prompt_file.read_text()

    bundles = list(signal_bundles)
    per_test_blocks = "\n".join(_format_evaluator_per_test_block(b) for b in bundles)
    if not per_test_blocks:
        per_test_blocks = "*(no tests in this batch — emit an empty verdicts array.)*\n"

    verdicts_path = spec_dir / "findings" / "verdicts.json"
    context = (
        "## EVALUATOR CONTEXT (TFactory Evaluator — Python)\n\n"
        f"Concrete paths for this run:\n\n"
        f"- spec_dir:    `{spec_dir}`\n"
        f"- project_dir: `{project_dir}` (read-only via Glob/Grep)\n"
        f"- write verdicts to: `{verdicts_path}`\n\n"
        f"Number of generated tests to evaluate: {len(bundles)}\n\n"
        "Files you can read:\n\n"
        f"- `{spec_dir / 'context' / 'aifactory_spec.md'}`\n"
        f"- `{spec_dir / 'context' / 'diff.patch'}` (may not exist)\n"
        f"- `{spec_dir / 'test_plan.json'}` (the full Planner output)\n"
        f"- each test file listed below (under `{spec_dir / 'tests'}`)\n\n"
        "## Pre-computed signals per test\n\n"
        f"{per_test_blocks}\n"
        "---\n\n"
    )
    return context + body
