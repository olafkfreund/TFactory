---
status: approved
issue: 1340
author: olafkfreund
---

# Intent: mark Swift explicitly not supported in TFactory

## Problem

TFactory vendors the hub's Swift language descriptor
(`apps/backend/tools/runners/languages/swift.yaml`, unit lane `available: true`),
so the lane registry reports Swift as a supported unit-lane language
(`get_tool_for_lane("swift", "unit")` returns an xctest ToolSpec,
`languages_supporting_lane("unit")` lists swift). But there is no in-cluster
Swift lane: no `run_swift_lane_via_nix` in `agents/nix_env.py`, no
`frameworks/swift`, no Swift filter in the evaluator. A completed Swift subtask
matches no lane filter and a Swift-only plan ends as `evaluated_empty`, which
reads like "nothing wrong" rather than "cannot verify this".

## Proposed outcome

TFactory says plainly that Swift is not supported: the registry refuses Swift
with a reason naming #1340, and a verify whose plan carries a completed Swift
subtask fails (`evaluator_failed`) with that reason instead of going empty or
silently dropping the Swift work.

## Affected users and systems

TFactory backend (lane registry, evaluator). Plans for the native iOS + Android
demo fleet. No other repo.

## Constraints

- The vendored `swift.yaml` is byte-exact with the hub canonical (verification-core
  drift gate); it must not be edited here. The descriptor's toolchain facts stay
  true (AIFactory's flake generation still uses them).
- No macOS in the fleet: SwiftUI/UIKit/simulator/xcodebuild are out of reach regardless.

## Open questions

None. The user decided: mark Swift explicitly not supported. A library-only
Swift lane can be a new issue once measured on the cluster.
