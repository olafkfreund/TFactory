---
status: approved
issue: 1407
intent: intent/2026-10-22-1407-nix-batched-run-artifacts.md
---

# Spec: persist Nix batched lane junit and coverage

## Design

The lane is not the problem, and neither is packing. Here is the evidence:

- `nix_env._build_pytest_cmd` writes `--junitxml` and `--cov-report=xml` to
  `/work/.tf_pytest/` in the Job.
- Unpacked: `/work` is the PVC co-mount of the scratch dir.
- Packed: `KubeJobSandbox._pack_lane` tars all of `/work` back
  (`tar -czf ... -C /work .`), and `_collect_lane` extracts it into the same `workdir`
  (the scratch dir).
- In both modes `run_pytest_lane_via_nix` then copies `.tf_pytest/{junit,coverage}.xml`
  to `_new_junit_artifacts_dir()` and returns them as `junit_xml_path` and
  `coverage_xml_path`.

The drop is in `evaluator._nix_batched_stability` and `evaluator._nix_batched_signals`.
Both ignore those two fields, so this gap existed before packing and is not a packing
regression.

Fix: in both functions, once `res is not None`, call the existing
`_with_durable_artifacts(spec_dir, test_file, res)`. It routes each path through
`_persist_run_artifact` into `findings/_run_artifacts/<stem>/`, which
`_measured_coverage` already reads. A missing file stays `None`, so coverage stays
unmeasured.

The Jest branch of `_nix_batched_stability` is out of scope: the Jest lane writes no
pytest coverage.

## Alternatives rejected

- **Copy inside `run_pytest_lane_via_nix`.** That function does not know the spec's
  findings layout, and it would duplicate `_persist_run_artifact`.
- **Change `_measured_coverage` to read `/tmp/tf-nixjunit`.** That location is not
  durable: it is swept after 6h and is not pushed back with the spec.
- **Change pack/collect.** It already round-trips `.tf_pytest/`.

## Risks

- The extra copy is two small files per subtask.
- The coverage report comes from the stability reruns only. Mutant runs pass no
  `--cov-report`, so a mutant cannot overwrite it.

## Verification

- Unpacked test: a fake sandbox writes into the co-mounted scratch.
- Packed test: a fake sandbox packs the scratch with `artifact_store._tar_workspace`,
  extracts it into a pod dir, writes the reports there, tars `/work` back and
  `_safe_extract`s it into the workdir, which is the `_pack_lane`/`_collect_lane` shape.
- Both tests drive the real `run_pytest_lane_via_nix` and `_nix_batched_signals`, then
  assert that `_run_artifacts/<stem>/` has both files and that `_measured_coverage`
  returns a number.
- A test with no reports asserts `(None, None)`.
