# M4 Acceptance — AOP-020 half A (WP-2.1a)

Date: 2026-10-09.
Delivery branch: `esf/hop-1.0`.
Implementation commit: `c3d7c70`.
Scope: task provenance, case-package importer, grouping/dedup. **Half B**
(sealed store, holdout access ledger, split assignment) is task-35 and is not
implemented or claimed here.

## Frozen contract

`src/hop/contracts/evaluation.py` is the interface the pack tasks and half B
code against. It defines:

| Record | Fields |
|---|---|
| `CaseGroup` | `group_id`, `repository`, `commit_shas`, `patch_digests`, `created_at` |
| `CasePackage` | `case_id`, `group_id`, `root`, `task_path`, `public_tests`, `hidden_ref`, `visible_tests`, `provenance_path`, `repository_manifest_path`, `environment_lock_path`, `content_digest`, `created_at` |
| `SourceProvenance` | `case_id`, `origin`, `repository`, `commit_sha`, `ancestry`, `patch_digest`, `changed_files`, `authored_at`, `committed_at`, `baseline`, `generator` |
| `RepositoryManifest` | `repository`, `commit_sha`, `entries[]`, `file_count`, `digest` |
| `RepositoryEntry` | `path`, `blob_sha`, `size_bytes`, `sha256` |
| `EnvironmentLock` | `python`, `platform`, `dependencies`, `note` |

**`CasePackage.hidden_ref` is an opaque token.** Half A emits
`sealed-unassigned:<case_id>`; half B's sealed store replaces it with the token
that resolves the holdout. Pack code must never resolve sealed paths from it,
and half A creates no sealed storage.

Published machine-readable schemas: `specs/case-group.schema.json`,
`specs/case-package.schema.json`, `specs/source-provenance.schema.json`.

## Package layout

```
<case_id>/task.yaml
<case_id>/prompt.md
<case_id>/repository.manifest.json
<case_id>/environment.lock.json
<case_id>/source-provenance.json
<case_id>/public-fixtures/...
<case_id>/visible-tests/...
<out_root>/groups.json
```

## Acceptance evidence

| Gate | Command | Observed result |
|---|---|---|
| Focused suite | `PYTHONPATH=src .venv/bin/python -m pytest tests/test_evaluation_provenance_import.py tests/test_evaluation_provenance_negative.py -o addopts= -q` | 17 passed |
| CLI + foundation CLI | `... pytest tests/test_evaluation_provenance_import.py tests/test_evaluation_provenance_negative.py tests/test_foundation_cli.py -o addopts= -q` | 44 passed |
| Unit suite | `scripts/dev test --suite unit` | 307 passed, 92 deselected, exit 0 |
| Lint / format | `ruff check` and `ruff format --check` on the changed sources and tests | clean |
| Type check | `scripts/dev check` | exit 0, 0 findings (47 files) |
| Determinism | import the same local repo into two output roots and re-import into the first | byte-identical trees; identical `group_id`/`case_id` sets |
| Related commits | repo with two `Case: issue-1` commits plus one `Case: issue-2` commit | group sizes `[1, 2]` — related commits together, unrelated separate |
| Near-duplicate | two branches applying the same change with one differing line (similarity 0.8) | exactly one 2-commit group |
| Schema validation | generated `CaseGroup`/`CasePackage`/`SourceProvenance` validated against the published schemas | PASS |
| CLI | `hop evaluation import <repo> --out <dir>`; `--groups FILE`; bad spec | exit 0 with JSON payload; explicit spec yields one group; malformed spec exits 2 with `invalid_group_spec` |
| Adversarial | malformed provenance, missing package file, tampered content, inconsistent writer inputs | all rejected explicitly (`ProvenanceError`/`CaseImportError`) |

Import is local-only: the importer runs `git` with `LC_ALL=C`, `TZ=UTC` and
`GIT_CONFIG_NOSYSTEM=1`, reads only the supplied path, and performs no network
access.

## Grouping rules (deterministic)

Two commits in the same repository are grouped when any of: identical normalized
patch digest; near-duplicate patches (same changed-file set, changed-line
Jaccard similarity >= 0.8); or the same non-empty issue reference (commit
trailer, `AOP-###`, or branch topic) within `--window-minutes` (default 180).
`group_id` is a sha256 over the repository plus the sorted commit shas and patch
digests, so a group is stable and is never re-split by a later import.
`case_id` is derived from `group_id`. Written files contain no wall-clock value.

## Unexecuted / out of scope

* Sealed storage, holdout access ledger, and split assignment (AOP-020 half B,
  task-35). No holdout enforcement is claimed.
* Live local-model acceptance (owner input O2 absent).
* Optimization/activation work (M5+); not started.

## Compatibility limitations

The change is additive: a new `hop evaluation` command group and a new package.
No existing command, exit code, schema, historical evidence byte, or lock file
was changed. `hidden_ref` is a placeholder token until half B lands; consumers
must treat it as opaque in both cases.
