"""aop CLI: validate-profile, discover, run, report (M0/M1)."""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))


def cmd_validate_profile(args) -> int:
    from aop.bundle import compile_bundle
    from aop.runner import load_deployment, qualify_pi_for_m1
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
    from aop.runner import Runner
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


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="aop")
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
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
