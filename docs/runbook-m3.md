# M3 runbook — profiles, compiler, and APM

All state is local. The default HOP home is `.hop/` at the repository root
(gitignored); override with `HOP_HOME`. Inference stays on loopback.

## 1. Import canonical components

```bash
export PYTHONPATH=src
hop registry import components        # idempotent
hop registry verify                   # per-component integrity evidence
hop component list --type skill
```

Component sources under `components/` are the reviewable inputs. Importing
copies content into the content-addressed store; editing a source and
re-importing the same version is refused (bump the version).

## 2. Validate / resolve / lock

```bash
hop profile validate examples/profiles/coding.yaml
hop profile lock examples/profiles/coding.yaml      # writes <profile>.hop.lock
hop profile deps examples/profiles/coding.yaml      # dependency graph
hop profile explain examples/profiles/debugging.yaml
```

`--set model_family=glm` overrides a variant selector for a resolution without
editing the profile.

## 3. Compile and run

```bash
hop profile compile examples/profiles/coding.yaml --out /tmp/pi-out
hop run --case debug-offbyone --profile examples/profiles/debugging.yaml
hop run show <run-id>          # profile/lock/compiled digests
hop run replay-check <run-id>  # is the locked profile still materializable?
```

The runner fails the run as a durable `infra_error` before agent execution if
the profile has a missing dependency, cycle, policy violation, an unavailable
component, or an undeclared model deployment.

## 4. Export and verify

```bash
hop profile export examples/profiles/coding.yaml --target apm --out /tmp/apm
hop profile verify-export /tmp/apm --profile examples/profiles/coding.yaml
```

If the `apm` CLI is installed, validate further in a **copy** (APM writes its
own lockfile):

```bash
cp -a /tmp/apm /tmp/apm-pack && cd /tmp/apm-pack
apm lock
apm pack -o ./build     # HOP content becomes a Claude plugin bundle
```

HOP remains authoritative for profile/evaluation identity; APM only packages.

## 5. End-to-end demo

```bash
PYTHONPATH=src .venv-m1/bin/python scripts/demo_m3.py --real --apm --record
```

- `--real` executes a real local-model Pi run from the locked `debugging`
  profile.
- `--apm` runs `apm lock` + `apm pack` on a copy.
- `--record` writes `docs/evidence/m3/demo-report.json` (machine-local paths
  replaced with placeholders).

## Failure codes

| Code | Meaning |
|---|---|
| `missing_dependency` | a referenced component is not registered |
| `dependency_cycle` | component dependency cycle |
| `version_conflict` | no version satisfies a constraint |
| `ambiguous_component` | same name under multiple component types |
| `unsupported_component_type` | reference type does not match stored type |
| `ambiguous_variant` | more than one variant matched |
| `malformed_skill_package` | selected variant has no content |
| `incompatible_harness` | component declares a different harness set |
| `missing_required_tool` | skill tool not allowed by the tool policy |
| `policy_violation` | lower layer tried to weaken mandatory policy |
| `forbidden_override` | a forbidden key was declared |
| `floating_reference` | a lock still contains a range |
| `stale_digest` | locked digest differs from the registry |
| `duplicate_component` | two locked components share an identity |
| `historical_component_unavailable` | materialization input missing/mutated |
| `target_capability_missing` | target cannot carry a required primitive |
| `missing_skill_resource` | declared resource/script absent from content |
| `duplicate_resource_collision` | two stages emitted the same path |
| `unsupported_target` | unknown/unimplemented compiler target |
| `unknown_model_deployment` | profile names an undeclared deployment |
