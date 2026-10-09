# HOP 1.0.0 — release notes (DRAFT, NOT RELEASED)

> **Status: skeleton / not a release.** No tag, no artifact publication, and no
> milestone acceptance is claimed by this file. `pyproject.toml` still declares
> `version = "0.2.0"`. Publication and version bumping are explicit owner actions.
> Nothing here may be quoted as evidence that HOP 1.0.0 shipped.

## Summary

One-paragraph description of what the 1.0.0 release would deliver for the
single-user local coding workflow (scope per ADR-011 and `BACKLOG.yaml`).

## What is in scope for 1.0.0

- [ ] M0–M3 slices re-verified at the release tip (historical matrices under
      `docs/acceptance-m{1,2,3}.md` stay byte-identical; the renewed record is a
      new current-acceptance document).
- [ ] HOP-R01 foundation baseline and boundary review.
- [ ] HOP-R02 pinned offline developer toolchain (toolchain level).
- [ ] HOP-R03 typed lifecycle state and uniform CLI diagnostics.
- [ ] HOP-R04 boundary closure (egress broker, cgroup quotas, verifier isolation).
- [ ] HOP-R05 documentation consolidation and renewed M0–M3 acceptance.
- [ ] Offline release artifacts qualified (this repository:
      `docs/verification/offline-install.md`).

## Release-blocking status at the time of writing

Fill in from the current board immediately before release. Current known state:

- **R02 type gate**: toolchain is GREEN and hash-pinned; `scripts/dev check` is
  RED while task-8's files carry type findings. Not release-ready.
- **R04 boundary closure**: open (egress broker, cgroup quotas, verifier
  isolation); a scope reduction is not a passed safety gate.
- **R05**: documentation consolidation pending.
- **Local model (O2)**: not available; all live-model acceptance remains
  unexecuted and must be reported as such, never inferred from fixtures.
- **CI**: `.github/workflows/ci.yml` is a definition; it has not been executed on
  the delivery host. Do not cite CI as a passed gate until a real run exists.

## Artifacts

| Artifact | How built | Verified by |
|----------|-----------|-------------|
| `hop-<version>-py3-none-any.whl` | `scripts/build_release.sh` (offline, no isolation) | clean-venv install from `--no-index --require-hashes`, `hop --help`, `hop doctor`, `hop init` |
| `hop-<version>.tar.gz` (sdist) | same script | same run |

Hashes are recorded per run in `dist/MANIFEST.sha256`; artifacts are **not**
byte-reproducible because the build backend stamps archive timestamps. Do not
publish a hash collected from a different run.

## Upgrade / compatibility notes for 0.2 readers

- Command surface, exit codes, and path precedence for 1.0 are consolidated in
  `docs/runbook.md` (R05). Readers of the 0.2 runbook should treat the consolidated
  document as authoritative for 1.0 and the historical milestone documents as
  immutable records.
- Historical M1/M2 evidence and locks must remain readable; no migration may
  rewrite them.
- The offline toolchain pin is host-specific (`development-tools.lock.json`):
  the type checker runs under a hash-manifested CPython 3.13 environment with
  `--python-version 3.14`. CI uses a knowingly different path.

## Owner actions before publication (not performed here)

- [ ] Land R03/R04/R05 and re-run the full acceptance set at the frozen tip.
- [ ] Re-run `scripts/dev check` and require exit 0 in the recorded environment.
- [ ] Re-run the offline install qualification and record artifacts/hashes.
- [ ] Decide the actual version string and bump `pyproject.toml` / `CHANGELOG.md`.
- [ ] Tag and publish only after the above; outside this repository's tests.
