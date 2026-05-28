#!/usr/bin/env bash
#
# verify-fork.sh — sanity check after hard-forking AIFactory into TFactory.
#
# Asserts:
#   1. We are running from inside the TFactory repo root.
#   2. Obsolete modules (per the design plan's "Files to delete from the fork" list)
#      are actually gone.
#   3. Renamed paths exist under their new names (tfactory_server.py, test_plan/).
#   4. Key Python modules import cleanly (no broken imports left over from
#      deletions/renames).
#   5. No stray `aifactory` / `AIFactory` references survive outside the
#      documented allowlist of intentional cross-references.
#
# Exit codes:
#   0   all checks pass
#   1+  number of failed checks
#
# Usage:
#   ./scripts/verify-fork.sh
#   ./scripts/verify-fork.sh --no-import   # skip the Python import check
#                                          # (useful before deps are installed)
#
set -uo pipefail

# ---------- config ----------
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

# Modules to import (skip with --no-import). Adjust as the rewrite progresses.
PY_MODULES=(
    "apps.backend.mcp_server.tfactory_server"
    "apps.backend.test_plan.models"
    "apps.backend.context.project_analyzer"
)

# Files / dirs that MUST NOT exist after the fork (from design plan).
OBSOLETE_PATHS=(
    "apps/backend/agents/coder.py"
    "apps/backend/qa"
    "apps/backend/spec_runner.py"
    "apps/backend/spec_agents"
    "apps/backend/prompts/coder.md"
    "apps/backend/prompts/qa_reviewer.md"
    "apps/backend/prompts/qa_fixer.md"
    "apps/backend/prompts/complexity_assessor.md"
    "apps/backend/prompts/followup_planner.md"
    ".claude/skills/aifactory-spec"
    ".claude/skills/handover"
    "apps/backend/mcp_server/aifactory_server.py"
    "apps/backend/implementation_plan"
)

# Files / dirs that MUST exist after the fork.
REQUIRED_PATHS=(
    "apps/backend/mcp_server/tfactory_server.py"
    "apps/backend/test_plan"
    ".claude/skills/handover-to-tfactory/SKILL.md"
    ".mcp.json"
    "README.md"
    "LICENSE"
    "docs/_config.yml"
    "docs/index.md"
    ".agent-os/specs/2026-05-28-tfactory-mvp-walking-skeleton/spec.md"
)

# Allowlist of paths that MAY legitimately mention `aifactory` / `AIFactory`.
# These are the documented intentional cross-references — files that DESCRIBE
# the handover-from-AIFactory relationship (skills, MCP tool descriptions,
# specs, the Pages site, quarantined inherited tests). Adding to this list
# must be justified in a commit message.
ALLOWLIST_GLOBS=(
    "docs/"
    ".agent-os/"
    "README.md"
    "scripts/verify-fork.sh"
    ".claude/skills/handover-to-tfactory/"
    "companion-skills/"
    "apps/backend/agents/tools_pkg/tools/task_control.py"
    "apps/backend/workspaces/"
    # TFactory Planner files (Task 5, #6). Prompts + helpers + tests
    # legitimately describe the AIFactory→TFactory cross-reference
    # (planner reads ~/.aifactory/.../specs/.../ snapshot via Task 3).
    "apps/backend/agents/planner.py"
    "apps/backend/agents/gen_functional.py"
    "apps/backend/agents/evaluator.py"
    "apps/backend/agents/triager.py"
    "apps/backend/tools/git_writer.py"
    "apps/backend/tools/pr_comment.py"
    "apps/backend/prompts/"
    "apps/backend/prompts_pkg/"
    "tests/test_mcp_task_control.py"
    "tests/test_tfactory_mcp_tools.py"
    "tests/test_snapshotter.py"
    "tests/test_planner_prompts.py"
    "tests/test_planner.py"
    "tests/test_planner_integration.py"
    "tests/test_gen_functional.py"
    "tests/test_gen_functional_prompts.py"
    "tests/test_gen_functional_integration.py"
    "tests/test_evaluator.py"
    "tests/test_evaluator_prompts.py"
    "tests/test_evaluator_integration.py"
    "tests/test_triager.py"
    "tests/fixtures/planner_smoke/"
    "guides/"
    ".git/"
)

# ---------- args ----------
DO_IMPORT_CHECK=1
for arg in "$@"; do
    case "$arg" in
        --no-import) DO_IMPORT_CHECK=0 ;;
        -h|--help)
            sed -n '2,/^set/p' "$0" | sed 's/^# //; s/^#//'
            exit 0
            ;;
        *) echo "verify-fork.sh: unknown arg: $arg" >&2; exit 2 ;;
    esac
done

# ---------- runner ----------
FAIL=0
pass() { printf "  \033[32m✓\033[0m %s\n" "$1"; }
fail() { printf "  \033[31m✗\033[0m %s\n" "$1"; FAIL=$((FAIL+1)); }
section() { printf "\n\033[1m== %s ==\033[0m\n" "$1"; }

# ---------- 1. repo root ----------
section "repo root"
if [ -d "$REPO_ROOT/.git" ]; then
    pass "TFactory repo root: $REPO_ROOT"
else
    fail "not a git repo (no .git/ at $REPO_ROOT)"
fi
if [ "$(basename "$REPO_ROOT")" = "TFactory" ]; then
    pass "directory name is TFactory"
else
    fail "directory name is $(basename "$REPO_ROOT") (expected TFactory)"
fi

# ---------- 2. obsolete paths gone ----------
section "obsolete paths removed"
for p in "${OBSOLETE_PATHS[@]}"; do
    if [ -e "$p" ]; then
        fail "still exists: $p"
    else
        pass "gone: $p"
    fi
done

# ---------- 3. required paths exist ----------
section "required paths present"
for p in "${REQUIRED_PATHS[@]}"; do
    if [ -e "$p" ]; then
        pass "exists: $p"
    else
        fail "missing: $p"
    fi
done

# ---------- 4. Python import smoke ----------
if [ "$DO_IMPORT_CHECK" = "1" ]; then
    section "Python import smoke"
    if ! command -v python3 >/dev/null 2>&1; then
        fail "python3 not on PATH"
    else
        for mod in "${PY_MODULES[@]}"; do
            if python3 -c "import importlib; importlib.import_module('$mod')" 2>/dev/null; then
                pass "import $mod"
            else
                fail "import $mod"
            fi
        done
    fi
else
    section "Python import smoke (SKIPPED via --no-import)"
fi

# ---------- 5. stray aifactory references ----------
section "stray aifactory references outside allowlist"
if ! command -v rg >/dev/null 2>&1; then
    fail "ripgrep (rg) not on PATH — required for allowlist scan"
else
    IGNORE_ARGS=()
    for g in "${ALLOWLIST_GLOBS[@]}"; do
        IGNORE_ARGS+=("--glob" "!${g}**" "--glob" "!${g}")
    done
    # Search both lowercase and CamelCase. Source-only (rg respects .gitignore).
    HITS="$(rg --hidden --no-messages -l -e 'aifactory' -e 'AIFactory' "${IGNORE_ARGS[@]}" . 2>/dev/null || true)"
    if [ -z "$HITS" ]; then
        pass "no stray aifactory/AIFactory references"
    else
        fail "stray aifactory/AIFactory references in:"
        echo "$HITS" | sed 's/^/      /'
    fi
fi

# ---------- summary ----------
echo
if [ "$FAIL" -eq 0 ]; then
    printf "\033[32mverify-fork: PASS\033[0m\n"
    exit 0
else
    printf "\033[31mverify-fork: FAIL (%d check%s failed)\033[0m\n" "$FAIL" "$([ "$FAIL" -eq 1 ] || echo s)"
    exit "$FAIL"
fi
