#!/usr/bin/env bash
#
# create-pfactory-labels.sh — create the PFactory tag-taxonomy labels TFactory
# needs so handed-off testing issues carry valid labels (#194, epic #193).
#
# Idempotent: uses `gh label create --force` (create-or-update). Safe to re-run.
#
# Contract: PFactory repo docs/tag-taxonomy.md (v1). TFactory creates only the
# labels it must recognise on incoming issues. It REUSES existing `epic`, `task`,
# `backend`, and its horizon scheme `priority:now|next|later`. It does NOT create
# `priority:p*` or `sev:*` — those are mapped from the pfactory:meta block (#196).
#
# Usage:
#   scripts/create-pfactory-labels.sh [--repo owner/name] [--dry-run]
#
# Requires the `gh` CLI, authenticated with repo label write access.

set -euo pipefail

REPO=""
DRY_RUN=0
for arg in "$@"; do
  case "$arg" in
    --repo=*) REPO="${arg#--repo=}" ;;
    --repo) shift; REPO="${1:-}" ;;
    --dry-run) DRY_RUN=1 ;;
    -h|--help) sed -n '2,21p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
  esac
done

command -v gh >/dev/null || { echo "error: gh CLI not found" >&2; exit 1; }

REPO_ARGS=()
[[ -n "$REPO" ]] && REPO_ARGS=(--repo "$REPO")

# label | color (no #) | description
LABELS=(
  "pfactory|5319e7|Created by PFactory (governed: reviewed + human-approved)"
  "handoff:tfactory|0e8a16|Route to TFactory for test generation + execution"
  "type:testing|c5def5|Work category: testing"
  "type:software|c5def5|Work category: software"
  "type:infra|c5def5|Work category: infra"
  "type:cicd|c5def5|Work category: cicd"
  "type:feature|c5def5|Work category: feature"
  "type:hosting|c5def5|Work category: hosting"
  "type:product|c5def5|Work category: product"
  "plan-type:software-service|bfdadc|PFactory plan-type: software-service"
  "plan-type:data-pipeline|bfdadc|PFactory plan-type: data-pipeline"
  "plan-type:infra-change|bfdadc|PFactory plan-type: infra-change"
  "plan-type:generic-deliverable|bfdadc|PFactory plan-type: generic-deliverable"
)

echo "Creating ${#LABELS[@]} PFactory taxonomy labels${REPO:+ on $REPO}..."
for entry in "${LABELS[@]}"; do
  IFS='|' read -r name color desc <<<"$entry"
  if [[ "$DRY_RUN" -eq 1 ]]; then
    printf '  [dry-run] %-32s #%s  %s\n' "$name" "$color" "$desc"
    continue
  fi
  gh label create "$name" --color "$color" --description "$desc" --force "${REPO_ARGS[@]}"
done

echo "Done. (Reused: epic, task, backend, priority:now|next|later — not recreated.)"
