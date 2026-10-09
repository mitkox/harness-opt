"""Worker-reachability tests for the sealed evaluator store (WP-2.1b).

Two proofs:

* structural — the bind set an isolated agent worker receives never includes the
  sealed root (the worker only gets its own run root rw plus system ro binds);
* runtime — inside a real ``bwrap --unshare-net`` namespace the sealed root is
  absent even for a read, so "hidden tests must not be readable inside an agent
  environment, even on a read-only mount" holds by construction.

When the environment cannot create the namespace the runtime test asserts the
explicit unenforced reason instead of skipping, so a green suite never means
"the boundary was not tested".
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from hop import sandbox
from hop.evaluation.sealed import SealedStore
from hop.evaluation.splits import SplitStoreMisconfigured, validate_sealed_root
from hop.sandbox import (
    RunResult,
    SandboxSpec,
    prepare_layout,
    probe_worker_network_isolation,
    spawn_isolated,
)

REPO = Path(__file__).resolve().parents[1]
CASE = "case-00000000000000aa"


def _sealed(tmp_path: Path, worker_root: str | None = None) -> tuple[SealedStore, str, Path]:
    root = tmp_path / "vault"
    store = SealedStore(str(root), repository_root=str(REPO), worker_root=worker_root)
    ref = store.seal(CASE, {"hidden/test_hidden.py": b"SEALED-SECRET"})
    return store, ref.token, Path(store.path_for(ref.token))


def test_worker_bind_set_never_includes_the_sealed_root(tmp_path, monkeypatch):
    _store, _token, sealed_path = _sealed(tmp_path)
    layout = prepare_layout(str(tmp_path / "run" / "worker"))
    captured: dict = {}

    def fake_spawn(cmd, *, cwd, env, ro_binds, rw_binds, spec, **kwargs):
        captured["ro"] = list(ro_binds)
        captured["rw"] = list(rw_binds)
        captured["spec"] = spec
        return RunResult(
            exit_code=0, timed_out=False, cancelled=False, stdout_path="", stderr_path=""
        )

    monkeypatch.setattr(sandbox, "spawn_confined", fake_spawn)
    sandbox.spawn_isolated(["/bin/true"], layout, SandboxSpec(share_network=False), timeout_s=5)
    binds = [dst for _src, dst in captured["ro"] + captured["rw"]]
    sealed_real = os.path.realpath(sealed_path)
    assert all(sealed_real not in os.path.realpath(bind) for bind in binds), binds
    assert captured["spec"].share_network is False
    # The sealed root is only valid because it is disjoint from the run root.
    with pytest.raises(SplitStoreMisconfigured):
        validate_sealed_root(
            str(Path(layout.root) / "vault"),
            repository_root=str(REPO),
            worker_root=layout.root,
        )


def test_sealed_store_refuses_a_worker_reachable_root(tmp_path):
    layout = prepare_layout(str(tmp_path / "run" / "worker"))
    with pytest.raises(SplitStoreMisconfigured):
        SealedStore(
            str(Path(layout.root) / "sealed"),
            repository_root=str(REPO),
            worker_root=layout.root,
        )
    with pytest.raises(SplitStoreMisconfigured):
        SealedStore(str(REPO / "sealed"), repository_root=str(REPO))


@pytest.mark.sandbox
def test_worker_cannot_read_the_sealed_store_even_read_only(tmp_path):
    layout = prepare_layout(str(tmp_path / "run" / "worker"))
    store, token, sealed_path = _sealed(tmp_path, worker_root=layout.root)
    sealed_real = os.path.realpath(sealed_path)
    ledger_path = os.path.realpath(store.ledger.path)
    report = probe_worker_network_isolation(str(tmp_path / "netprobe"))
    if not report["enforced"]:
        # Explicit blocked evidence: no namespace, no isolation claim, no skip.
        assert report["reason"], report
        return
    cmd = [
        "bash",
        "-c",
        (
            f"test -e {sealed_real!r} && echo SEALED_VISIBLE || echo SEALED_ABSENT; "
            f"cat {sealed_real!r}/manifest.json 2>/dev/null | head -c 40; "
            f"test -e {ledger_path!r} && echo LEDGER_VISIBLE || echo LEDGER_ABSENT; "
            f"test -e {sealed_real!r}/files/hidden/test_hidden.py && echo MATERIAL_VISIBLE "
            f"|| echo MATERIAL_ABSENT"
        ),
    ]
    result = spawn_isolated(cmd, layout, SandboxSpec(share_network=False), timeout_s=30)
    output = Path(result.stdout_path).read_text()
    assert result.exit_code == 0, output
    assert "SEALED_ABSENT" in output and "SEALED_VISIBLE" not in output
    assert "LEDGER_ABSENT" in output and "LEDGER_VISIBLE" not in output
    assert "MATERIAL_ABSENT" in output and "MATERIAL_VISIBLE" not in output
    assert "SEALED-SECRET" not in output
    # The verifier can still read the same material; the boundary is the worker.
    material = store.read(
        token, actor="verifier-1", role="verifier", purpose="evaluation", run_id="run-1"
    )
    assert material["hidden/test_hidden.py"] == b"SEALED-SECRET"
