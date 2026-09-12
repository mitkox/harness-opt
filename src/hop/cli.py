"""hop CLI: profiles, components, runs, and investigation (M0-M3)."""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))


def _runs_dir() -> str:
    from hop.envcompat import RUNS_DIR_VARS, resolve_env
    from hop.runner import RUNS_DIR
    return resolve_env(*RUNS_DIR_VARS, default=RUNS_DIR)


def _print(payload) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True))


def _fail(message: str, code: int = 2) -> int:
    print(message, file=sys.stderr)
    return code


def cmd_validate_profile(args) -> int:
    from hop.bundle import compile_bundle
    from hop.runner import load_deployment, qualify_pi_for_m1
    try:
        deployment = load_deployment(args.model)
    except KeyError as exc:
        print(f"REFUSED: {exc} (unresolved model input)", file=sys.stderr)
        return 2
    harness = qualify_pi_for_m1()
    try:
        bundle, _ = compile_bundle(deployment, harness, args.prompt)
    except ValueError as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    if not bundle.deployable:
        print("REFUSED: draft target is not deployable", file=sys.stderr)
        return 2
    print(json.dumps({"deployable": True, "digest": bundle.digest,
                      "model": deployment.deployment_id,
                      "harness": f"{harness.harness.value}@{harness.version}"}, indent=2))
    return 0


def cmd_discover(_args) -> int:
    with open("profiles/models/local-inventory.json") as fh:
        models = json.load(fh)
    with open("profiles/harnesses/local-inventory.json") as fh:
        harnesses = json.load(fh)
    pair = None
    for dep in models["deployments"]:
        if dep["status"] == "qualified":
            pair = dep["deployment_id"]
            break
    pi = next((h for h in harnesses["harnesses"] if h["harness"] == "pi"), {})
    print(json.dumps({"local_pair": {"model": pair, "harness": f"pi@{pi.get('version')}"},
                      "deployments": len(models["deployments"])}, indent=2))
    return 0 if pair else 3


def cmd_run(args) -> int:
    from hop.runner import Runner
    runner = Runner(hop_home=args.home or None)
    overrides = {}
    for pair in getattr(args, "set", []) or []:
        if "=" in pair:
            key, value = pair.split("=", 1)
            overrides[key] = value
    report = runner.execute(args.case, model_alias=args.model, harness=args.harness,
                            timeout_s=args.timeout,
                            idempotency_key=args.idempotency_key or "",
                            profile=args.profile or "",
                            profile_target=getattr(args, "target", "pi") or "pi",
                            variant_overrides=overrides or None)
    keys = ["run_id", "case_id", "outcome", "verdict", "bundle_digest",
            "model_deployment_id", "agent_seconds", "total_seconds", "run_dir",
            "profile"]
    print(json.dumps({k: report[k] for k in keys if k in report}, indent=2))
    return 0 if report["outcome"] == "pass" else 1


def cmd_report(args) -> int:
    with open(os.path.join(args.run_dir, "report.json")) as fh:
        print(fh.read())
    return 0


def cmd_run_investigate(args) -> int:
    from hop import investigate as inv
    runs_dir = _runs_dir()
    run_dir = inv.find_run_dir(runs_dir, args.run_id)
    report = inv.show(run_dir)
    run_id = report["run_id"]
    if args.sub == "show":
        print(json.dumps(report, indent=2, sort_keys=True))
    elif args.sub == "trajectory":
        print(json.dumps(inv.trajectory(run_dir, args.event_type or ""),
                         indent=2, sort_keys=True))
    elif args.sub == "artifacts":
        print(json.dumps(inv.artifacts(run_dir, runs_dir, run_id),
                         indent=2, sort_keys=True))
    elif args.sub == "trace":
        print(json.dumps(inv.trace_view(run_dir), indent=2, sort_keys=True))
    elif args.sub == "verify":
        print(json.dumps(inv.verify_view(run_dir), indent=2, sort_keys=True))
    elif args.sub == "skills":
        print(json.dumps(inv.skills_view(run_dir), indent=2, sort_keys=True))
    elif args.sub == "tools":
        print(json.dumps(inv.tools_view(run_dir), indent=2, sort_keys=True))
    elif args.sub == "completeness":
        print(json.dumps(inv.completeness_view(run_dir, args.outcome or ""),
                         indent=2, sort_keys=True))
    elif args.sub == "replay-check":
        print(json.dumps(inv.replay_check(run_dir, runs_dir, run_id),
                         indent=2, sort_keys=True))
    else:
        print(f"unknown run subcommand {args.sub}", file=sys.stderr)
        return 2
    return 0


# -- M3 component / skill / profile / registry commands --------------------

def _parse_sets(pairs) -> dict:
    overrides = {}
    for item in pairs or []:
        if "=" not in item:
            raise ValueError(f"--set expects dimension=value, got {item!r}")
        key, value = item.split("=", 1)
        overrides[key.strip()] = value.strip()
    return overrides


def cmd_component_list(args) -> int:
    from hop import profiles as P
    from hop.contracts.profile import ComponentType

    ctype = ComponentType(args.type) if args.type else None
    _print(P.component_list(P.open_registry(args.home), ctype))
    return 0


def cmd_component_show(args) -> int:
    from hop import profiles as P

    name, _, version = args.ref.partition("@")
    _print(P.component_show(P.open_registry(args.home), name, version))
    return 0


def cmd_skill_list(args) -> int:
    from hop import profiles as P
    from hop.contracts.profile import ComponentType

    _print(P.component_list(P.open_registry(args.home), ComponentType.SKILL))
    return 0


def cmd_skill_inspect(args) -> int:
    from hop import profiles as P

    name, _, version = args.ref.partition("@")
    _print(P.component_show(P.open_registry(args.home), name, version))
    return 0


def cmd_registry_import(args) -> int:
    from hop import profiles as P
    from hop.components import import_directory

    registry = P.open_registry(args.home)
    imported = import_directory(registry, args.directory)
    _print({"imported": imported, "count": len(imported),
            "registry_root": P.registry_root(args.home)})
    return 0


def cmd_registry_verify(args) -> int:
    from hop import profiles as P

    registry = P.open_registry(args.home)
    results = registry.verify_all()
    _print({"ok": all(r["ok"] for r in results), "components": len(results),
            "results": results})
    return 0 if all(r["ok"] for r in results) else 1


def cmd_profile_validate(args) -> int:
    from hop import profiles as P

    result = P.validate_profile(args.profile, P.open_registry(args.home))
    _print(result)
    return 0 if result.get("valid") else 2


def cmd_profile_resolve(args) -> int:
    from hop import profiles as P
    from hop.resolver import ResolutionError

    registry = P.open_registry(args.home)
    try:
        profile = P.load_profile(args.profile)
        resolved = P.resolve(profile, registry, _parse_sets(args.set))
    except (P.ProfileError, ResolutionError, ValueError) as exc:
        code = getattr(exc, "code", "resolution_error")
        _print({"valid": False, "code": code, "error": str(exc)})
        return 2
    _print(resolved.model_dump(mode="json"))
    return 0


def cmd_profile_lock(args) -> int:
    from hop import profiles as P
    from hop.resolver import ResolutionError

    registry = P.open_registry(args.home)
    try:
        _, _, lock = P.lock_profile(args.profile, registry, target=args.target,
                                    overrides=_parse_sets(args.set), home=args.home)
    except (P.ProfileError, ResolutionError, ValueError) as exc:
        return _fail(f"REFUSED: {getattr(exc, 'code', 'resolution_error')}: {exc}")
    out = args.out or P.lock_path_for(args.profile)
    P.write_lock(lock, out)
    _print({"lock": out, "lock_digest": lock.lock_digest,
            "profile_digest": lock.profile_digest,
            "components": len(lock.components), "target": lock.compilation_target})
    return 0


def cmd_profile_inspect(args) -> int:
    from hop import profiles as P

    _print(P.inspect(args.profile, P.open_registry(args.home), target=args.target))
    return 0


def cmd_profile_deps(args) -> int:
    from hop import profiles as P

    _print(P.deps(args.profile, P.open_registry(args.home)))
    return 0


def cmd_profile_explain(args) -> int:
    from hop import profiles as P

    _print(P.explain(args.profile, P.open_registry(args.home), target=args.target))
    return 0


def cmd_profile_diff(args) -> int:
    from hop import profiles as P

    _print(P.diff_profiles(args.profile_a, args.profile_b, P.open_registry(args.home),
                           verbose=args.verbose))
    return 0


def cmd_profile_compile(args) -> int:
    from hop import profiles as P
    from hop.compiler import CompilerError
    from hop.resolver import ResolutionError

    registry = P.open_registry(args.home)
    try:
        _, _, lock, artifact, _ = P.compile_profile(
            args.profile, registry, target=args.target, out_dir=args.out,
            home=args.home)
    except (P.ProfileError, ResolutionError, CompilerError, ValueError) as exc:
        return _fail(f"REFUSED: {getattr(exc, 'code', 'compile_error')}: {exc}")
    _print({"target": args.target, "out_dir": args.out,
            "artifact_digest": artifact.artifact_digest,
            "lock_digest": lock.lock_digest,
            "profile_digest": lock.profile_digest,
            "files": [f.path for f in artifact.files],
            "warnings": artifact.warnings})
    return 0


def cmd_profile_materialize(args) -> int:
    from hop import profiles as P
    from hop.compiler import CompilerError
    from hop.resolver import ResolutionError

    try:
        result = P.materialize(args.reference, P.open_registry(args.home),
                               target=args.target, out_dir=args.out, home=args.home)
    except (P.ProfileError, ResolutionError, CompilerError, ValueError) as exc:
        return _fail(f"REFUSED: {getattr(exc, 'code', 'materialize_error')}: {exc}")
    _print(result)
    return 0


def cmd_profile_export(args) -> int:
    from hop import profiles as P
    from hop.resolver import ResolutionError

    registry = P.open_registry(args.home)
    try:
        summary = P.export(args.profile, registry, target=args.target,
                           out_dir=args.out, home=args.home)
    except (P.ProfileError, ResolutionError, ValueError) as exc:
        return _fail(f"REFUSED: {getattr(exc, 'code', 'export_error')}: {exc}")
    _print(summary)
    return 0


def cmd_profile_verify_export(args) -> int:
    from hop import profiles as P
    from hop.apm_export import ExportVerificationError

    try:
        result = P.verify_exported(args.path, registry=P.open_registry(args.home),
                                   source_profile=args.profile, home=args.home)
    except ExportVerificationError as exc:
        _print({"ok": False, "code": exc.code, "error": str(exc)})
        return 2
    _print(result)
    return 0


def _main(argv=None, prog: str = "hop") -> int:
    parser = argparse.ArgumentParser(prog=prog)
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("validate-profile")
    p.add_argument("--model", required=True)
    p.add_argument("--prompt", default="repair task")
    p.set_defaults(func=cmd_validate_profile)
    sub.add_parser("discover").set_defaults(func=cmd_discover)
    r = sub.add_parser("run")
    r.add_argument("--case", required=True)
    r.add_argument("--model", default="qwen-flash-next")
    r.add_argument("--harness", default="pi")
    r.add_argument("--timeout", type=float, default=None)
    r.add_argument("--idempotency-key", default="")
    r.add_argument("--profile", default="", help="M3 source profile to lock+compile")
    r.add_argument("--target", default="pi")
    r.add_argument("--set", action="append", default=[],
                   help="variant selector override, e.g. model_family=qwen")
    r.add_argument("--home", default="")
    r.set_defaults(func=cmd_run)
    rep = sub.add_parser("report")
    rep.add_argument("--run-dir", required=True)
    rep.set_defaults(func=cmd_report)
    for verb in ("show", "trajectory", "artifacts", "trace", "verify", "skills",
                 "tools", "completeness", "replay-check"):
        verb_p = sub.add_parser(f"run-{verb}")
        verb_p.add_argument("run_id")
        verb_p.add_argument("--event-type", default="")
        verb_p.add_argument("--outcome", default="")
        verb_p.set_defaults(func=cmd_run_investigate, sub=verb)

    # -- M3: components, skills, registry, profiles -----------------------
    cl = sub.add_parser("component-list")
    cl.add_argument("--type", default="")
    cl.add_argument("--home", default="")
    cl.set_defaults(func=cmd_component_list)
    cs = sub.add_parser("component-show")
    cs.add_argument("ref")
    cs.add_argument("--home", default="")
    cs.set_defaults(func=cmd_component_show)
    sl = sub.add_parser("skill-list")
    sl.add_argument("--home", default="")
    sl.set_defaults(func=cmd_skill_list)
    si = sub.add_parser("skill-inspect")
    si.add_argument("ref")
    si.add_argument("--home", default="")
    si.set_defaults(func=cmd_skill_inspect)
    ri = sub.add_parser("registry-import")
    ri.add_argument("directory", nargs="?", default="components")
    ri.add_argument("--home", default="")
    ri.set_defaults(func=cmd_registry_import)
    rv = sub.add_parser("registry-verify")
    rv.add_argument("--home", default="")
    rv.set_defaults(func=cmd_registry_verify)

    pv = sub.add_parser("profile-validate")
    pv.add_argument("profile")
    pv.add_argument("--home", default="")
    pv.set_defaults(func=cmd_profile_validate)
    pr = sub.add_parser("profile-resolve")
    pr.add_argument("profile")
    pr.add_argument("--set", action="append", default=[])
    pr.add_argument("--home", default="")
    pr.set_defaults(func=cmd_profile_resolve)
    pl = sub.add_parser("profile-lock")
    pl.add_argument("profile")
    pl.add_argument("--target", default="pi")
    pl.add_argument("--out", default="")
    pl.add_argument("--set", action="append", default=[])
    pl.add_argument("--home", default="")
    pl.set_defaults(func=cmd_profile_lock)
    pi = sub.add_parser("profile-inspect")
    pi.add_argument("profile")
    pi.add_argument("--target", default="pi")
    pi.add_argument("--home", default="")
    pi.set_defaults(func=cmd_profile_inspect)
    pd = sub.add_parser("profile-deps")
    pd.add_argument("profile")
    pd.add_argument("--home", default="")
    pd.set_defaults(func=cmd_profile_deps)
    pe = sub.add_parser("profile-explain")
    pe.add_argument("profile")
    pe.add_argument("--target", default="pi")
    pe.add_argument("--home", default="")
    pe.set_defaults(func=cmd_profile_explain)
    pdi = sub.add_parser("profile-diff")
    pdi.add_argument("profile_a")
    pdi.add_argument("profile_b")
    pdi.add_argument("--verbose", action="store_true")
    pdi.add_argument("--home", default="")
    pdi.set_defaults(func=cmd_profile_diff)
    pc = sub.add_parser("profile-compile")
    pc.add_argument("profile")
    pc.add_argument("--target", default="pi")
    pc.add_argument("--out", default="")
    pc.add_argument("--home", default="")
    pc.set_defaults(func=cmd_profile_compile)
    pm = sub.add_parser("profile-materialize")
    pm.add_argument("reference")
    pm.add_argument("--target", default="pi")
    pm.add_argument("--out", default="")
    pm.add_argument("--home", default="")
    pm.set_defaults(func=cmd_profile_materialize)
    px = sub.add_parser("profile-export")
    px.add_argument("profile")
    px.add_argument("--target", default="apm")
    px.add_argument("--out", default="")
    px.add_argument("--home", default="")
    px.set_defaults(func=cmd_profile_export)
    pve = sub.add_parser("profile-verify-export")
    pve.add_argument("path")
    pve.add_argument("--profile", default="")
    pve.add_argument("--home", default="")
    pve.set_defaults(func=cmd_profile_verify_export)
    args = parser.parse_args(argv)
    return args.func(args)


def main(argv=None) -> int:
    # `hop run show <run-id>` style: `run` doubles as execution (`--case`) and
    # investigation (`run show|trajectory|... <run-id>`). Disambiguate before
    # argparse, which cannot give one subcommand two shapes.
    _VERBS = {"show", "trajectory", "artifacts", "trace", "verify", "skills",
              "tools", "completeness", "replay-check"}
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) >= 2 and argv[0] == "run" and argv[1] in _VERBS:
        verb = argv[1]
        return _main([f"run-{verb}"] + argv[2:])
    # `hop profile lock ...`, `hop component list ...`, etc. Namespaces use
    # `namespace verb` in user-facing form and `namespace-verb` internally.
    if len(argv) >= 2 and argv[0] in ("profile", "component", "skill", "registry"):
        namespace, verb = argv[0], argv[1]
        if not verb.startswith("-"):
            return _main([f"{namespace}-{verb}"] + argv[2:])
    return _main(argv)


def legacy_main(argv=None) -> int:
    """Deprecated `aop` alias: same implementation, warns, same semantics."""
    print("hop: warning: the `aop` command is deprecated; use `hop` "
          "(alias removal target: M4)", file=sys.stderr)
    return main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
