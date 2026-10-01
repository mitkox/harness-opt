# HOP 0.2 Developer and Operator Runbook

HOP is a single-user local coding tool. There is no enterprise mode, account
setup, organization configuration, hosted write-back, or service stack to deploy.
The owner publishes code outside HOP; evaluation only produces local evidence.
Active profiles use local-safety. Historical org_policy identifiers are retained
for file compatibility, not user-management features. See ADR-011.

## Offline Setup

The currently evidenced environment is Linux x86-64 with Python 3.14. Supply the
approved wheelhouse; bootstrap never downloads packages. Existing `.venv-m1`
environments remain usable. New environments live in `.venvs/` with a `.venv`
launcher link. Never delete old environments while their processes are active.

```sh
HOP_WHEELHOUSE=/path/to/approved/wheels scripts/dev bootstrap
./hop --help
./hop doctor
scripts/dev test --suite unit
scripts/dev test --suite contract
scripts/dev test --suite sandbox
scripts/dev check
```

`scripts/dev bootstrap --runtime` installs control-plane and trusted-verifier
dependencies, including pytest. It excludes developer-only JSON Schema tooling.
Use a separate link such as `HOP_LOCK_VENV=.venv-runtime` to keep
the developer environment. `--run-tests` tests a replacement before switching;
it needs usable local sockets and bubblewrap. A failure preserves the old link.
`scripts/build_lock_env.sh` delegates to this non-destructive bootstrap.

`development-tools.lock.json` pins the observed Ruff binary. Mypy and its offline
transitive lock are not supplied; `check` returns BLOCKED until an administrator
imports and pins approved artifacts. An arbitrary executable on PATH is not
accepted as a pinned tool. No type-check success is currently claimed.

## Workspace and First Run

```sh
./hop init
./hop deployment list
./hop registry import components
./hop profile validate examples/profiles/coding.yaml
./hop run start --case debug-offbyone --model REGISTERED_DEPLOYMENT
./hop run list
```

`init` creates `hop.toml` exclusively and refuses to replace existing configuration.
`doctor` checks presence of prerequisites; it does not qualify a model/harness or
install anything. Inventory records must describe actual pinned local deployments.
`discover` displays stored inventory; live discovery remains an explicit operation.

An exact deployment ID or unique registered alias is required. A profile may
supply its deployment reference. Synthetic fixtures are for tests and cannot
qualify a live deployment. The first verified run needs a compatible Pi build,
local model, and functioning sandbox/verifier; agent success text is insufficient.

Paths resolve from explicit flags, HOP environment variables, `hop.toml`, then
checkout defaults. Relative configured paths are relative to the workspace.
Use `--workspace`, `--home`, and `--runs-dir` before or after command groups.
`HOP_DEPLOYMENTS` selects an inventory file and `HOP_VERIFIER_ROOT` the verifier
store. Endpoint overrides remain prohibited.

## Investigation and Profiles

```sh
./hop run show RUN_ID
./hop run trajectory RUN_ID --cursor 0 --limit 100
./hop run completeness RUN_ID
./hop run compare LEFT_RUN RIGHT_RUN --format json
./hop profile explain examples/profiles/debugging.yaml
./hop profile lock examples/profiles/coding.yaml --out /tmp/coding.hop.lock
./hop profile compile examples/profiles/coding.yaml --out /tmp/pi-profile
```

Trajectory cursors count raw events, including events filtered from a page.
`next_cursor` advances over scanned events. The bounded reader validates the
whole ledger's integrity and refuses concurrent modification or a pending write.
The original unpaged output remains available for compatibility. A legacy ledger
without a chain is identified explicitly; inspection never creates a chain.

Comparison displays recorded evidence and completeness, with no statistical
improvement claim. Incomplete or corrupted artifacts are never quietly omitted.
Terminal output defaults to text; redirected output defaults to JSON. Use
`--format` to choose explicitly. Diagnostics go to stderr. New execution uses
0 for pass, 1 for verified failure, 2 for invalid input, 3 for blocked/inconclusive
or infrastructure results, and 130 for cancellation. Legacy execution preserves
its 0/1 behavior and warns about deprecated syntax.

## Acceptance and Contribution

`scripts/dev test --suite all` selects every non-live test. `--suite live` checks
the installed Pi build against the existing qualified contract. The currently
observed 1.0.0 differs from the prior 0.85.1 contract, so live qualification is
blocked. Fixtures use synthetic identities and temporary stores; they are not
evidence of model quality. Historical tests copy committed M1 evidence.

`scripts/dev acceptance` runs static and non-live gates, then explicitly reports
the outstanding independent-review and local-model requirements. It cannot
declare complete milestone acceptance. See `acceptance-foundation.md` and
`foundation-review.md` before advancing the roadmap.

Use the pinned environment and focused tests, then run the full applicable suite.
Commit small logical changes, preserve user work and historical evidence, and
record unavailable prerequisites. Never run old evidence-writing acceptance
scripts against committed evidence paths without isolating their output first.
