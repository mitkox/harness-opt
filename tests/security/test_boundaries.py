"""Adversarial boundary tests (AOP-003/AOP-008/AOP-010). Defensive, lab-only."""
import os

import pytest

from aop import sandbox, verification
from aop.policy import assert_local_url


def test_hostile_env_never_reaches_child(tmp_path):
    layout = sandbox.prepare_layout(str(tmp_path / "w"))
    hostile = {"OPENAI_API_KEY": "sk-live-123", "HTTP_PROXY": "http://x:1",
               "HTTPS_PROXY": "http://x:1", "SSH_AUTH_SOCK": "/tmp/agent.sock",
               "AWS_SECRET_ACCESS_KEY": "shh", "AOP_PI_BASE_URL": "http://127.0.0.1:8000/v1"}
    res = sandbox.spawn_isolated(
        ["bash", "-c", "env | sort"], layout, sandbox.SandboxSpec(),
        extra_env=hostile)
    out = open(res.stdout_path).read()
    assert res.exit_code == 0
    for secret in ("sk-live-123", "AWS_SECRET_ACCESS_KEY", "agent.sock"):
        assert secret not in out
    assert "HTTP_PROXY" not in out and "http_proxy" not in out.replace("HTTP_PROXY", "")


def test_verifier_child_has_no_credentials(tmp_path):
    snap = str(tmp_path / "snap")
    os.makedirs(snap)
    probe = os.path.join(snap, "probe_env.py")
    with open(probe, "w") as fh:
        fh.write("import os\ndef test_no_secrets():\n"
                 "    assert 'OPENAI_API_KEY' not in os.environ\n"
                 "    assert 'HTTP_PROXY' not in os.environ\n"
                 "    assert 'SSH_AUTH_SOCK' not in os.environ\n")
    os.environ["OPENAI_API_KEY"] = "sk-should-not-propagate"
    hidden = str(tmp_path / "sealed")
    os.makedirs(hidden)
    with open(os.path.join(hidden, "seal_marker.txt"), "w") as fh:
        fh.write("sealed")
    try:
        res = verification.run_hidden_tests(
            "r-sec", "a1", snap, hidden,
            ["python3", "-m", "pytest", probe, "-q", "-p", "no:cacheprovider"],
            60.0, verification.hash_hidden_dir(hidden))
    finally:
        del os.environ["OPENAI_API_KEY"]
    assert res.verdict.value == "pass", res.details["log_tail"][-500:]


def test_hidden_root_outside_any_workspace(tmp_path):
    hidden_root = os.path.abspath(".hidden")
    layout = sandbox.prepare_layout(str(tmp_path / "w"), snapshot_src="")
    assert os.path.commonpath([hidden_root, os.path.abspath(layout.workspace)]) != hidden_root
    assert not os.path.islink(os.path.abspath(layout.workspace))


def test_no_cloud_inference_path_exists():
    import inspect
    import aop.inference as inf
    import aop.runner as runner
    src = inspect.getsource(inf) + inspect.getsource(runner)
    assert "api.openai.com" not in src
    assert "fallback" not in src.lower() or "no fallback" in src.lower()


def test_offhost_urls_refused_everywhere():
    for url in ("https://api.openai.com/v1", "http://10.1.2.3:8000/v1",
                "http://[fd00::1]:8000/v1", "ftp://127.0.0.1/x"):
        with pytest.raises(ValueError):
            assert_local_url(url)
