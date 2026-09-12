"""hop CLI: validate-profile, discover, run, report (M0/M1) + run investigation (M2)."""
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
    runner = Runner()
    report = runner.execute(args.case, model_alias=args.model, harness=args.harness,
                            timeout_s=args.timeout,
                            idempotency_key=args.idempotency_key or "")
    print(json.dumps({k: report[k] for k in
                      ("run_id", "case_id", "outcome", "verdict", "bundle_digest",
                       "model_deployment_id", "agent_seconds", "total_seconds",
                       "run_dir")}, indent=2))
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
    return _main(argv)


def legacy_main(argv=None) -> int:
    """Deprecated `aop` alias: same implementation, warns, same semantics."""
    print("hop: warning: the `aop` command is deprecated; use `hop` "
          "(alias removal target: M4)", file=sys.stderr)
    return main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
