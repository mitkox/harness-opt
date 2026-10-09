# HOP 1.0 Developer and Operator Runbook

HOP is a single-user local coding tool. There is no enterprise mode, account
setup, organization configuration, hosted write-back, or service stack to deploy.
The owner publishes code outside HOP; evaluation only produces local evidence.
Active profiles use local-safety. Historical org_policy identifiers are retained
for file compatibility, not user-management features. See ADR-011.

Foundation acceptance (HOP-R01–R05) is closed at the delivery tip recorded in
[acceptance-foundation-renewal.md](acceptance-foundation-renewal.md); M4 is the
next roadmap item (`next_roadmap_item_after_acceptance: AOP-020` in
`BACKLOG.yaml`). M5–M9 and release qualification are **not** delivered and must
not be claimed.

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
`scripts/build_lock_env.sh` is a thin compatibility alias for this bootstrap and
performs no destructive action; no destructive bootstrap path exists. Bootstrap
refuses to replace an unmanaged `.venv` and preserves the previous environment on
any failure.

`development-tools.lock.json` pins two approved local artifacts: Ruff 0.16.10 and
the mypy 2.3.1 `local_environment`. The type checker runs under the pinned
CPython 3.13.13 interpreter that owns the library tree, with
`--python-version 3.14` targeting the evidenced platform; the interpreter file and
a 1149-file sha256 manifest are verified before every run, so a different `PATH`
mypy is never accepted. `bootstrap-tools` installs only pinned wheel artifacts.
At the acceptance tip `scripts/dev check` exits 0 with no findings (47 files).
The cp313 deviation, artifact provenance, and the CI-vs-local deviation are in
[verification/o1-request.md](verification/o1-request.md) and
[verification/offline-install.md](verification/offline-install.md).

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

## Boundary Model (1.0)

Three boundaries are enforced at run time, and each reports honestly when
enforcement is unavailable. No configuration may weaken them.

- **Worker network.** Agent workers run with no network namespace
  (`bwrap --unshare-net`). Inference is reachable only through the local
  `hop.broker` over a per-run `AF_UNIX` socket (mode `0600` under the run root,
  bind-mounted into the worker) carrying a fresh
  `secrets.token_urlsafe(32)` per-attempt bearer token. The broker starts only
  after served-model attestation passes, pins the attested loopback upstream and
  model id, allow-lists `POST /v1/chat/completions` and `GET /v1/models|/props`,
  refuses redirects, absolute-form targets and `CONNECT`, and writes one audit
  line per request. A failed no-network probe is a terminal `infra_error`
  (`worker_network_isolation_unavailable`); there is no shared-network fallback.
- **Aggregate resource quotas.** Delegated cgroup v2 limits are applied per run
  where the host delegates them, and reported as `enforced`, `limited`, or
  `blocked` together with the exact `unenforced[]` dimensions. A dimension the
  host cannot enforce is never claimed as enforced; `HOP_REQUIRE_QUOTAS=1`
  escalates an unavailable quota to `infra_error` instead of a silent pass.
- **Verifier isolation.** Candidate code runs in its own process/namespace over a
  frozen snapshot; the trusted host process never imports candidate code. The
  verifier reads only the frozen snapshot and the sealed hidden case, and forged
  success reports are rejected.

Negative/adversarial coverage lives in `tests/security/`; see
[threat-model.md](threat-model.md) and ADR-005/ADR-012/ADR-013.

## Acceptance and Contribution

`scripts/dev test --suite all` selects every non-live test. `--suite live` checks
the installed Pi build against a qualified contract; it is **unexecuted** here
because no qualified local model/Pi pair is available (O2 absent). Fixtures use
synthetic identities and temporary stores and are not evidence of model quality.
Historical `0.85.1` literals in fixtures are intentionally version-agnostic and
say nothing about any installed build. The current command/result matrix,
including every unexecuted item and its reason, is in
[acceptance-foundation-renewal.md](acceptance-foundation-renewal.md); the
historical milestone matrices remain immutable.

`scripts/dev acceptance` runs static and non-live gates, then explicitly reports
the outstanding independent-review and local-model requirements. It cannot
declare complete milestone acceptance. See `acceptance-foundation.md` and
`foundation-review.md` for the historical foundation review.

Use the pinned environment and focused tests, then run the full applicable suite.
Commit small logical changes, preserve user work and historical evidence, and
record unavailable prerequisites. Never run old evidence-writing acceptance
scripts against committed evidence paths without isolating their output first.

## Coding and Debugging Packs (AOP-021)

Workflow packs bind a benchmark case to its independent success evidence:
hidden behavioral tests, regression tests, and API/scope review. Enabled packs:

| Pack | Kind | Evidence |
| --- | --- | --- |
| `pack:coding-feature` (`feature-slugify`) | feature | hidden behavioral tests + API/scope review |
| `pack:coding-refactor` (`refactor-dedupe`) | refactor | hidden regression tests + API/scope review |
| `pack:debug-median` (`debug-offbyone`) | reproducer | hidden behavioral tests (shipped fixture) |
| `pack:debug-wordcount` (`debug-wordcount`) | reproducer | hidden regression tests (shipped fixture) |

Validate one candidate patch on frozen snapshots (baseline first, then the
patched candidate, then the overfitting signal):

```python
from hop.evaluation.packs import PACKS_BY_ID, validate_candidate
pack = PACKS_BY_ID["pack:debug-median"]
result = validate_candidate(
    pack,
    case_dir="benchmarks/development/debug-offbyone",
    hidden_root=".hidden/hidden-tests",
    candidate_patch=open("benchmarks/packs/candidates/debug-offbyone/passing.patch").read(),
    work_root="/tmp/pack-work",
)
```

Run the full example matrix (passing, failing, inconclusive; plus the overfit
fixture) and write one trajectory/completeness artifact per example:

```sh
PYTHONPATH=src .venv/bin/python -c "
from hop.evaluation.packs import run_examples
run_examples(benchmarks_root='benchmarks', hidden_root='.hidden/hidden-tests',
             work_root='/tmp/pack-work', out_root='/tmp/pack-out')"
```

Semantics: a pack passes only when every verifier passes; a candidate that
passes the visible command while a trusted verifier fails and whose patch
touches only candidate-visible tests is rejected as `visible_test_overfit`.
Baseline problems are classified before the candidate runs: a baseline that
cannot collect is `infra_error`, and a baseline whose hidden state contradicts
the pack expectation is an invalid case reported as `inconclusive` — never
blamed on the candidate. Hidden fixtures stay evaluator-owned under
`.hidden/hidden-tests/<case_id>/` and are never copied into a candidate
workspace. Live model execution remains unexecuted until owner input O2.
