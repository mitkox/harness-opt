"""Adversarial boundary tests (AOP-003/AOP-008/AOP-010). Defensive, lab-only.

These exercise the real mount/PID namespace boundary: the agent process must
not be able to read hidden verifier material, write it, modify verifier code,
or reach another run's workspace.
"""

import os
from pathlib import Path

import pytest

from hop import sandbox, verification
from hop.policy import assert_local_url

requires_bwrap = pytest.mark.skipif(not sandbox.bwrap_available(), reason="bwrap unavailable")


def _sandbox_result(cmd, tmp_path, name="w"):
    layout = sandbox.prepare_layout(str(tmp_path / name))
    spec = sandbox.SandboxSpec(share_network=False)
    return sandbox.spawn_isolated(cmd, layout, spec), layout


def test_hostile_env_never_reaches_child(tmp_path):
    layout = sandbox.prepare_layout(str(tmp_path / "w"))
    hostile = {
        "OPENAI_API_KEY": "sk-live-123",
        "HTTP_PROXY": "http://x:1",
        "HTTPS_PROXY": "http://x:1",
        "SSH_AUTH_SOCK": "/tmp/agent.sock",
        "AWS_SECRET_ACCESS_KEY": "shh",
        "HOP_PI_BASE_URL": "http://127.0.0.1:8000/v1",
        "AOP_PI_BASE_URL": "http://127.0.0.1:8000/v1",
    }
    res = sandbox.spawn_isolated(
        ["bash", "-c", "env | sort"], layout, sandbox.SandboxSpec(), extra_env=hostile
    )
    out = Path(res.stdout_path).read_text()
    assert res.exit_code == 0
    for secret in ("sk-live-123", "AWS_SECRET_ACCESS_KEY", "agent.sock"):
        assert secret not in out
    assert "HTTP_PROXY" not in out and "http_proxy" not in out.replace("HTTP_PROXY", "")
    assert "HOP_PI_BASE_URL" not in out and "AOP_PI_BASE_URL" not in out


@requires_bwrap
def test_agent_cannot_read_hidden_verifier_material(tmp_path):
    hidden_root = os.path.abspath(".hidden")
    marker = os.path.join(hidden_root, "hidden-tests", "debug-offbyone", "test_hidden_median.py")
    assert os.path.exists(marker)  # exists for the evaluator
    cmd = [
        "bash",
        "-c",
        (
            f"test -e {marker!r} && echo LEAKED || echo ABSENT; "
            f"test -e {hidden_root!r} && echo ROOT_LEAKED || echo ROOT_ABSENT"
        ),
    ]
    res, _ = _sandbox_result(cmd, tmp_path)
    out = Path(res.stdout_path).read_text()
    assert "LEAKED" not in out and "ABSENT" in out
    assert "hidden-tests" not in out


@requires_bwrap
def test_agent_cannot_write_hidden_verifier_material(tmp_path):
    target = os.path.abspath(".hidden/hidden-tests/debug-offbyone/pwned")
    cmd = ["bash", "-c", f"echo x > {target!r} 2>&1 || echo WRITE_DENIED"]
    res, _ = _sandbox_result(cmd, tmp_path)
    assert not os.path.exists(target)
    assert "WRITE_DENIED" in Path(res.stdout_path).read_text()


@requires_bwrap
def test_agent_cannot_modify_verifier_code(tmp_path):
    target = os.path.abspath("src/hop/verification.py")
    cmd = ["bash", "-c", f"echo pwn >> {target!r} 2>&1 || echo WRITE_DENIED"]
    res, _ = _sandbox_result(cmd, tmp_path)
    assert "WRITE_DENIED" in Path(res.stdout_path).read_text()
    with open(target) as fh:
        assert "pwn" not in fh.read()


@requires_bwrap
def test_agent_cannot_access_another_workspace(tmp_path):
    parent = tmp_path / "runs"
    other = parent / "run-other" / "workspace"
    os.makedirs(other)
    (other / "secret.txt").write_text("other run secret")
    layout = sandbox.prepare_layout(str(parent / "run-mine" / "worker"))
    spec = sandbox.SandboxSpec(share_network=False)
    cmd = ["bash", "-c", f"cat {str(other / 'secret.txt')!r} 2>&1 || echo DENIED"]
    res = sandbox.spawn_isolated(cmd, layout, spec)
    out = Path(res.stdout_path).read_text()
    assert "other run secret" not in out
    assert "DENIED" in out or "No such file" in out


def test_hidden_root_outside_any_workspace(tmp_path):
    hidden_root = os.path.abspath(".hidden")
    layout = sandbox.prepare_layout(str(tmp_path / "w"), snapshot_src="")
    assert os.path.commonpath([hidden_root, os.path.abspath(layout.workspace)]) != hidden_root
    assert not os.path.islink(os.path.abspath(layout.workspace))


def test_verifier_child_has_no_credentials(tmp_path):
    snap = str(tmp_path / "snap")
    os.makedirs(snap)
    hidden = str(tmp_path / "sealed")
    os.makedirs(hidden)
    probe = (
        "def test_no_secrets():\n"
        "    assert 'OPENAI_API_KEY' not in __import__('os').environ\n"
        "    assert 'HTTP_PROXY' not in __import__('os').environ\n"
        "    assert 'SSH_AUTH_SOCK' not in __import__('os').environ\n"
    )
    with open(os.path.join(hidden, "test_probe.py"), "w") as fh:
        fh.write(probe)
    with open(os.path.join(hidden, "verifier.json"), "w") as fh:
        fh.write(
            '{"verifier_id":"probe","version":"1","test_files":["test_probe.py"],'
            '"expected_tests":["test_probe.py::test_no_secrets"],'
            '"mandatory_tests":["test_probe.py::test_no_secrets"],'
            '"minimum_test_count":1}'
        )
    os.environ["OPENAI_API_KEY"] = "sk-should-not-propagate"
    try:
        res = verification.run_verification("r-sec", "a1", snap, hidden, timeout_s=90)
    finally:
        del os.environ["OPENAI_API_KEY"]
    assert res.verdict.value == "pass", res.details.get("log_tail", "")[-500:]


def test_no_cloud_inference_path_exists():
    import hop.inference as inf
    from hop import runner

    src = Path(inf.__file__).read_text() + Path(runner.__file__).read_text()
    assert "api.openai.com" not in src


def test_offhost_urls_refused_everywhere():
    for url in (
        "https://api.openai.com/v1",
        "http://10.1.2.3:8000/v1",
        "http://[fd00::1]:8000/v1",
        "ftp://127.0.0.1/x",
    ):
        with pytest.raises(ValueError):
            assert_local_url(url)
