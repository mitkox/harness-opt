# Migrating to HOP 1.0.0

HOP 1.0.0 is the major release in which the 0.2 compatibility layer retires.
The compatibility layer existed so a rename (`aop` → `hop`, `AOP_*` → `HOP_*`)
could land without breaking existing invocations; it is now removed. Every
removed form fails closed with a diagnostic that points back at this document
rather than silently doing something different.

Nothing here changes stored evidence. Historical run directories, bundles,
schemas, and acceptance records stay byte-identical and remain readable.

## 1. The `aop` console script is gone

`aop` was an alias for `hop` that printed a deprecation warning. It is no longer
installed.

| Before (0.2) | After (1.0) |
| --- | --- |
| `aop run show <run-id>` | `hop run show <run-id>` |
| `aop doctor` | `hop doctor` |

After upgrading, `aop` is not on `PATH`. Remove any wrapper scripts that call it
and replace the command name with `hop`.

## 2. The `AOP_*` environment fallback is gone

`HOP_*` is the only supported prefix. A removed `AOP_*` variable that is set now
raises `hop.envcompat.LegacyEnvRemoved` (the CLI reports
`legacy_env_removed` and exits 2) instead of being honoured.

| Removed (0.2) | Replacement (1.0) |
| --- | --- |
| `AOP_RUNS_DIR` | `HOP_RUNS_DIR` |
| `AOP_VERIFIER_ROOT` | `HOP_VERIFIER_ROOT` |
| `AOP_COLLECTOR_DOWN` | `HOP_COLLECTOR_DOWN` |
| `AOP_WHEELHOUSE` | `HOP_WHEELHOUSE` |
| `AOP_LOCK_VENV` | `HOP_LOCK_VENV` |

Rename the variables in your shell profile, CI configuration, or service unit,
then remove the old spellings. `HOP_*` values are unaffected.

Some variables are read while HOP is importing (for example
`HOP_VERIFIER_ROOT`); if the removed spelling is set, startup fails with the
same migration message rather than continuing with an ambiguous value.

### The `AOP_PI_BASE_URL` scrub is intentionally retained

`AOP_PI_BASE_URL` and `HOP_PI_BASE_URL` are still stripped from worker
environments and ignored by the runner. That is a local-only security guarantee
(M1), not compatibility sugar: an endpoint override must never reach a worker or
change which model is contacted, whatever it is called. Do not "clean up" that
scrub when removing the rest of the `AOP_*` handling.

## 3. Legacy command spellings are gone

The flat command forms and the old `hop run --case ...` shorthand are removed.
They now exit 2 with `legacy_command_removed` and a rewrite hint.

| Removed (0.2) | Replacement (1.0) |
| --- | --- |
| `hop run --case demo` | `hop run start --case demo` |
| `hop run-start --case demo` | `hop run start --case demo` |
| `hop profile-compile --model m` | `hop profile compile --model m` |
| `hop component-list` | `hop component list` |
| `hop run-list` | `hop run list-runs` |

The nested command tree is the only supported interface:
`hop <namespace> <verb> [options]`.

## 4. What did not change

- Evidence and schema bytes: historical run records, bundles, locks, and
  acceptance matrices are untouched and still readable.
- Verdict semantics: the trusted verifier remains authoritative and candidate
  text still cannot substitute for it.
- Local-only inference: no hosted fallback, no endpoint override, no network
  installation during evaluation.
- The evidence-chain backup and restore commands landed for 1.0.

## Checking a migration

```sh
hop doctor                      # canonical invocation, exits 0 when healthy
env | grep '^AOP_'              # must be empty
hop run show <existing-run-id>  # historical runs stay readable
```

If a removed form is used, HOP prints the replacement and the path to this
document; it does not fall back to the legacy behaviour.
