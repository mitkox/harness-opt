# ADR-002: Pi headless JSONL adapter instead of RPC mode

Date: 2026-09-12.

BUILD_PLAN §6 proposes Pi's RPC mode (JSON over stdin/stdout). The pinned Pi
0.85.1 supports RPC as an interactive mode, but the supported *headless*
entry point is `pi -p --mode json`: it processes one prompt non-interactively
and streams the same JSONL session events to stdout.

Decision: the M1 adapter (`PiJsonAdapter`, revision `pi-json-adapter-v1`)
spawns `pi -p --mode json --no-session` per attempt with an isolated HOME,
config, and workspace, parses JSONL events, and maps them to trajectory
events. Prompt acceptance (process spawn / first event) is recorded as
`harness.accepted`, never as completion. Cancellation is SIGTERM to the
process group; crash cleanup reaps and preserves partial events.

Interactive RPC (`--mode rpc`) remains an option for M5 multi-turn harnesses;
it was not used because per-attempt isolation is cleaner with one-shot
headless runs.

## Revision: local-only endpoint derivation (post-review)

The adapter no longer reads `AOP_PI_BASE_URL`. The provider base URL, model id,
context window, and `maxTokens` derive only from the registered
`ModelDeployment` record; `policy.assert_local_url` rejects anything that is
not loopback, and `AOP_PI_BASE_URL` is scrubbed from worker environments.
Before a Pi run the runner calls `inference.verify_served_model`, which
attests that the endpoint actually serves the deployment's `model_id` (and
weight path when reported) and fails closed otherwise. Capability claims come
from real `--version`/`--help`/termination probes stored as
`probe_evidence`, not from parsing a version string. See ADR-005 for the
verifier boundary.

