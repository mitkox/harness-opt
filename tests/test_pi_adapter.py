"""Pi adapter + scripted harness tests (AOP-009). Hermetic fake `pi` binary."""
import json
import os
import stat
import threading

from aop.contracts.base import new_id
from aop.harnesses.pi_adapter import PiJsonAdapter, native_to_trajectory_kind
from aop.harnesses.scripted import ScriptedHarness

FAKE_PI = """#!/bin/bash
echo '{"type":"session","version":3,"id":"s1"}'
echo '{"type":"agent_start"}'
if [ "$1" = "hang" ]; then sleep 30; fi
if [ "$1" = "fail" ]; then echo '{"type":"error","error":"boom"}'; exit 1; fi
echo '{"type":"message_end","message":{"role":"assistant","content":[{"type":"text","text":"I fixed it"}]}}'
"""


def _fake_pi(tmp_path):
    path = str(tmp_path / "pi")
    with open(path, "w") as fh:
        fh.write(FAKE_PI)
    os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)
    return path


def test_probe_real_pi():
    caps = PiJsonAdapter().probe()
    assert caps.supports_headless is True
    assert caps.build_version != "unknown"
    assert "0.85" in caps.build_version


def test_prepare_isolates_config(tmp_path):
    adapter = PiJsonAdapter(executable=_fake_pi(tmp_path))
    session = adapter.prepare("sha256:" + "ab" * 32, str(tmp_path / "run"), "w1")
    assert session.extra_env["PI_CODING_AGENT_DIR"].startswith(str(tmp_path))
    assert os.path.exists(os.path.join(session.extra_env["PI_CODING_AGENT_DIR"], "models.json"))
    # global user config untouched (no test writes outside tmp)


def test_prompt_acceptance_is_not_completion(tmp_path):
    adapter = PiJsonAdapter(executable=_fake_pi(tmp_path))
    session = adapter.prepare("sha256:" + "ab" * 32, str(tmp_path / "run"), "w1")
    ws = str(tmp_path / "ws")
    os.makedirs(ws)
    out = adapter.start("do thing", session, ws, str(tmp_path / "out.jsonl"),
                        str(tmp_path / "err.log"), 20.0, threading.Event())
    # exit 0 + narration present, but adapter reports only harness status...
    assert out.terminal_status == "exited"
    assert out.agent_claim == "I fixed it"
    # ...and the claim/model separation: claim text is not a verdict field
    assert not hasattr(out, "verdict")


def test_native_mapping_separates_claims():
    etype, auth = native_to_trajectory_kind({"type": "message_end"})
    assert auth == "agent_report"
    etype2, auth2 = native_to_trajectory_kind({"type": "agent_start"})
    assert auth2 == "trusted_observer"


def test_global_home_not_contaminated(tmp_path):
    adapter = PiJsonAdapter(executable=_fake_pi(tmp_path))
    session = adapter.prepare("sha256:" + "ab" * 32, str(tmp_path / "run"), "w1")
    assert os.path.expanduser("~/.pi") not in session.extra_env["PI_CODING_AGENT_DIR"]


def test_scripted_arms():
    ws_out = "/tmp"
    import tempfile
    for behavior, status in [("succeed", "exited"), ("fail", "exited"),
                             ("hang", "timeout"), ("claim_success", "exited")]:
        h = ScriptedHarness(behavior, delay_s=0.1 if behavior != "hang" else 5.0)
        with tempfile.TemporaryDirectory() as td:
            out = h.start("p", h.prepare("d", td, "w"), td,
                          f"{td}/o", f"{td}/e", 0.5, threading.Event())
        assert out.terminal_status == status, behavior
    h = ScriptedHarness("hang", delay_s=30.0)
    cancel = threading.Event()
    import threading as th
    box = {}
    with tempfile.TemporaryDirectory() as td:
        t = th.Thread(target=lambda: box.update(
            r=h.start("p", h.prepare("d", td, "w"), td, f"{td}/o", f"{td}/e",
                         30.0, cancel)))
        t.start()
        cancel.set()
        t.join(10)
    assert box["r"].terminal_status == "cancelled"
