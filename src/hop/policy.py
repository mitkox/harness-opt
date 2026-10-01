"""Local-only policy enforcement (AOP-003, AOP-007).

Network enforcement, not prompts, restricts inference destinations:
only registered loopback endpoints are allowed; cloud fallback,
redirects off-localhost, and proxy-mediated exfiltration fail closed.
"""

from __future__ import annotations

import ipaddress
import os
from urllib.parse import urlparse

POLICY_ID = "local-default-v2"

# Environment variables that must never reach an agent worker: credentials
# and anything that could reroute traffic off the host.
SCRUB_ENV_PREFIXES = (
    "OPENAI_",
    "ANTHROPIC_",
    "AWS_",
    "AZURE_",
    "GOOGLE_",
    "GEMINI_",
    "QWEN_",
    "XIAOMI_",
    "CLOUDFLARE_",
    "HF_",
    "HUGGINGFACE_",
    "GITHUB_",
    "GITLAB_",
)
SCRUB_ENV_EXACT = {
    "API_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "HF_TOKEN",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "http_proxy",
    "https_proxy",
    "ALL_PROXY",
    "all_proxy",
    "NO_PROXY",
    "no_proxy",
    "SSH_AUTH_SOCK",
    "GIT_ASKPASS",
    # Endpoint redirection for Pi is controlled by the deployment record only.
    # Both the canonical and legacy spellings are refused (M1 local-only).
    "HOP_PI_BASE_URL",
    "AOP_PI_BASE_URL",
}

ALLOWED_HOSTS = {"127.0.0.1", "localhost", "::1"}
WORKER_ENV_KEYS = {
    "PATH",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TZ",
    "TERM",
    "NO_COLOR",
    "HOME",
    "XDG_CACHE_HOME",
    "TMPDIR",
    "PI_CODING_AGENT_DIR",
    "PI_OFFLINE",
    "PI_CODING_AGENT_SESSION_DIR",
}


def is_loopback_url(url: str) -> bool:
    try:
        host = urlparse(url).hostname or ""
    except ValueError:
        return False
    if host in ALLOWED_HOSTS:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def assert_local_url(url: str) -> str:
    """Fail closed unless the URL resolves to this host. No redirects, no DNS."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"refusing non-http(s) endpoint: {url!r}")
    if not is_loopback_url(url):
        raise ValueError(f"refusing non-local endpoint: {url!r}")
    return url


def scrub_worker_env(env: dict[str, str]) -> dict[str, str]:
    """Remove credentials, proxies, and agent-socket inheritance for workers."""
    clean: dict[str, str] = {}
    for key, value in env.items():
        if key not in WORKER_ENV_KEYS:
            continue
        if key in SCRUB_ENV_EXACT:
            continue
        if key.startswith(SCRUB_ENV_PREFIXES):
            continue
        clean[key] = value
    # Keep a minimal safe PATH etc. as provided; enforce offline Pi behavior.
    clean["PI_OFFLINE"] = "1"
    return clean


def check_no_proxy_leak(env: dict[str, str]) -> None:
    for key in SCRUB_ENV_EXACT:
        if key in env:
            raise ValueError(f"worker env still contains {key}")
    for key in env:
        if key.startswith(SCRUB_ENV_PREFIXES):
            raise ValueError(f"worker env still contains {key}")


def current_process_proxy_state() -> dict[str, str]:
    """Report proxy vars of the control-plane process (for offline checks)."""
    names = ["HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"]
    return {k: v for k, v in os.environ.items() if k in names}
