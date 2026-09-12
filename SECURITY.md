# Security policy

HOP executes third-party coding-agent harnesses, model-generated code, and
repository test suites inside local sandboxes. Treat every security boundary
in this repository as load-bearing: the agent worker, the trusted verifier,
the trajectory ledger, and the promotion path.

## Reporting a vulnerability

**Do not open a public issue for a suspected vulnerability or a working
exploit.** Public exploit issues put downstream users at risk and will be
handled as incidents first.

- If GitHub private vulnerability reporting is enabled for this repository,
  use **Security → Report a vulnerability**.
- Otherwise, open a minimal issue titled `SECURITY: <short topic>` that
  describes the affected area only (for example `verifier isolation`), and
  state that details will follow through a private channel. Do not include
  exploit code, bypass transcripts, or hidden-test content.

Include where possible: HOP version/tag, harness and model deployment under
test, the boundary you believe is violated, and reproduction steps that stay
inside an isolated lab (no public targets, no real credentials).

## Scope and safe testing

Authorized defensive testing covers this repository's own fixtures:
malicious repository instructions, forged test logs, fake tool-call JSON,
secret-disclosure prompts, unauthorized path access, hidden-test access,
policy edits, malicious package lifecycle scripts, worker-escape attempts in
controlled fixtures, and observer outage. These fixtures live under
`tests/security/` and `tests/test_verification.py` and must only run against
local synthetic cases.

Out of scope: scanning public targets, probing infrastructure you do not own,
exfiltrating data, and any live writes (comments, pushes, deployments).

## Supported releases (alpha)

During the `v0.1.0-alpha` period there are no long-term support branches.
Security fixes land on the default branch and are tagged as new pre-release
versions. Alpha users should pin a tag, keep local model weights and harness
binaries pinned, and assume the local-only threat model documented in
`docs/threat-model.md`: `bwrap` namespace isolation for the invoking user,
loopback-only inference, no multi-tenant hosting guarantees.
