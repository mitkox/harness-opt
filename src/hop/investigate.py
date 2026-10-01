"""M2 investigation interface (AOP-015): run show/trajectory/artifacts/trace/
verify/skills/tools/completeness/replay-check over durable run directories.
"""

from __future__ import annotations

import json
import os


def find_run_dir(runs_dir: str, run_id: str) -> str:
    from .files import contained_path, identifier

    identifier(run_id)
    direct = contained_path(runs_dir, run_id)
    if os.path.isdir(direct):
        return direct
    # idempotency-key or prefix lookup across run dirs
    matches = []
    for name in sorted(os.listdir(runs_dir)) if os.path.isdir(runs_dir) else []:
        full = contained_path(runs_dir, name)
        if not os.path.isdir(full):
            continue
        if name.startswith(run_id):
            matches.append(full)
            continue
        report = os.path.join(full, "report.json")
        if os.path.exists(report):
            try:
                with open(report) as fh:
                    rep = json.load(fh)
                if rep.get("run_id") == run_id:
                    return full
            except (OSError, ValueError):
                continue
    if len(matches) == 1:
        return matches[0]
    if matches:
        raise ValueError(f"ambiguous run prefix {run_id!r}")
    raise KeyError(f"no run {run_id!r} under {runs_dir}")


def _load_json(path: str):
    with open(path) as fh:
        return json.load(fh)


def show(run_dir: str) -> dict:
    rep = _load_json(os.path.join(run_dir, "report.json"))
    manifest = {}
    if os.path.exists(os.path.join(run_dir, "manifest.json")):
        manifest = _load_json(os.path.join(run_dir, "manifest.json"))
    return {
        "run_id": rep.get("run_id"),
        "case_id": rep.get("case_id"),
        "outcome": rep.get("outcome"),
        "verdict": rep.get("verdict"),
        "error_class": rep.get("error_class", ""),
        "bundle_digest": rep.get("bundle_digest"),
        "model_deployment_id": rep.get("model_deployment_id"),
        "model_deployment_digest": rep.get("model_deployment_digest"),
        "harness_digest": rep.get("harness_digest"),
        "trace_id": rep.get("trace_id", ""),
        "agent_seconds": rep.get("agent_seconds"),
        "total_seconds": rep.get("total_seconds"),
        "resources": rep.get("resources", {}),
        "telemetry_incomplete": rep.get("telemetry_incomplete", False),
        "spool": rep.get("spool", {}),
        "verifier": rep.get("verifier", {}),
        "inputs": rep.get("inputs", {}),
        "profile": rep.get("profile", {}),
        "run_dir": run_dir,
        "harness_version": (manifest.get("harness", {}) or {}).get("version", ""),
    }


def trajectory(run_dir: str, event_type: str = "") -> list[dict]:
    from hop.trajectories import EventLedger

    ledger = EventLedger(os.path.join(run_dir, "events.jsonl"))
    out = []
    for evt in ledger.read_all():
        if event_type and evt.event_type != event_type:
            continue
        out.append(
            {
                "event_type": evt.event_type,
                "authority": evt.source.authority.value,
                "source": evt.source.id,
                "seq": evt.source_sequence,
                "agent": evt.agent_id,
                "trace_id": evt.trace_id,
                "span_id": evt.span_id,
                "attributes": evt.attributes,
                "payload_refs": evt.payload_refs,
                "skill": evt.skill_id,
                "tool": evt.tool_id,
                "classification": evt.data_classification,
            }
        )
    return out


def artifacts(run_dir: str, runs_dir: str, run_id: str) -> list[dict]:
    from hop.storage import ArtifactStore

    store = ArtifactStore(os.path.join(runs_dir, "artifacts"), create=False)
    refs = store.list_refs(run_id)
    for ref in refs:
        ref["verifies"] = store.verify(ref["digest"])
    return refs


def trace_view(run_dir: str) -> dict:
    for name in ("trace-otel.json", "trace.json"):
        path = os.path.join(run_dir, name)
        if os.path.exists(path):
            payload = _load_json(path)
            payload["_source"] = name
            return payload
    return {"spans": [], "note": "no trace exported"}


def verify_view(run_dir: str) -> dict:
    for name in ("evaluation.json", "scope.json", "error.json", "cancellation.json"):
        path = os.path.join(run_dir, name)
        if os.path.exists(path):
            return {"file": name, "payload": _load_json(path)}
    return {"file": "", "payload": {}}


def skills_view(run_dir: str) -> list[dict]:
    return [e for e in trajectory(run_dir) if e["event_type"].startswith("skill.")]


def tools_view(run_dir: str) -> list[dict]:
    return [
        e
        for e in trajectory(run_dir)
        if e["event_type"].startswith("tool.") or e["event_type"].startswith("harness.pi.tool")
    ]


def completeness_view(run_dir: str, outcome: str = "") -> dict:
    from hop.trajectories import EventLedger

    ledger = EventLedger(os.path.join(run_dir, "events.jsonl"))
    rep = {}
    if os.path.exists(os.path.join(run_dir, "report.json")):
        rep = _load_json(os.path.join(run_dir, "report.json"))
    outcome = outcome or rep.get("outcome", "pass")
    policy = ledger.completeness_for_outcome(outcome)
    policy["legacy_completeness"] = rep.get("completeness", {})
    policy["integrity"] = ledger.verify_integrity()
    return policy


def replay_check(run_dir: str, runs_dir: str, run_id: str) -> dict:
    """Report whether required identities/artifacts are still available.

    Never claims deterministic replay for stochastic model generation.
    """
    rep = _load_json(os.path.join(run_dir, "report.json"))
    replay = rep.get("replay", {})
    from hop.storage import ArtifactStore

    store = ArtifactStore(os.path.join(runs_dir, "artifacts"), create=False)
    artifact_status = []
    refs = store.list_refs(run_id)
    available = {r["digest"] for r in refs if store.verify(r["digest"])}
    manifest = {}
    if os.path.exists(os.path.join(run_dir, "manifest.json")):
        manifest = _load_json(os.path.join(run_dir, "manifest.json"))
    checks = {
        "repo_snapshot": bool(replay.get("repo_snapshot_digest")),
        "environment": bool(replay.get("environment_digest")),
        "model_deployment": bool(replay.get("model_deployment_digest")),
        "harness": bool(replay.get("harness_digest")),
        "bundle": bool(replay.get("bundle_digest")),
        "verifier": bool(replay.get("verifier_id")),
        "manifest_present": bool(manifest),
        "snapshot_dir_present": os.path.isdir(os.path.join(run_dir, "snapshot")),
        "artifacts_available": len(available),
    }
    for ref in sorted(available):
        artifact_status.append({"digest": ref, "available": True})
    # M3: profile availability (only for profile-aware runs; legacy runs skip).
    profile = rep.get("profile", {}) or replay.get("profile_digest")
    profile_report = {"present": False}
    if isinstance(profile, dict) and profile.get("profile_digest"):
        profile_report = _profile_availability(profile)
        checks["profile_store"] = profile_report.get("stored", False) and profile_report.get(
            "materializable", False
        )
    missing = [k for k, v in checks.items() if k not in ("artifacts_available",) and not v]
    return {
        "reproducible_identities": checks,
        "missing": missing,
        "artifacts": artifact_status,
        "profile": profile_report,
        "replayable": not missing,
        "note": "identities are pinned; stochastic model generation is not claimed deterministic",
    }


def _profile_availability(profile: dict) -> dict:
    """Can the exact locked agent configuration be materialized locally?"""
    digest = profile.get("profile_digest", "")
    report = {
        "present": True,
        "profile_digest": digest,
        "lock_digest": profile.get("lock_digest", ""),
        "compiled_target": profile.get("compiled_target", ""),
        "compiled_target_digest": profile.get("compiled_target_digest", ""),
    }
    # The registry root is recorded in the run's profile identity so replay
    # verification uses the same store/catalog as the original run.
    registry_root = profile.get("registry_root", "")
    home = os.path.dirname(registry_root) if registry_root else None
    try:
        from hop import profiles as P

        # Prefer the exact lock digest; fall back to the source profile digest.
        lock_digest = profile.get("lock_digest", "")
        stored = P.load_persisted(lock_digest or digest, home=home)
        resolved = stored.get("resolved", {})
        lock = stored.get("lock", {})
        report["stored"] = True
        report["materialized_lock_digest"] = lock.get("lock_digest", "")
        report["components"] = len(resolved.get("components", []))
        report["materializable"] = _components_available(lock, registry_root)
    except Exception as exc:  # noqa: BLE001
        report["stored"] = False
        report["materializable"] = False
        report["reason"] = str(exc)
    return report


def _components_available(lock: dict, registry_root: str) -> bool:
    """Every locked component must still exist with the same digest."""
    if not registry_root or not os.path.isdir(registry_root):
        return False
    try:
        from hop.registry import ComponentRegistry

        registry = ComponentRegistry(registry_root)
        for comp in lock.get("components", []):
            record, _ = registry.get(comp["name"], comp["version"], comp["type"])
            if record.record_digest != comp["digest"]:
                return False
        return True
    except Exception:  # noqa: BLE001
        return False
