"""In-namespace relay for the inference broker (ADR-012).

The worker runs under ``bwrap --unshare-net``, so only its own loopback exists.
Pi speaks HTTP over loopback, while the broker listens on a bind-mounted
``AF_UNIX`` socket. This supervisor bridges the two:

1. brings the namespace loopback up (stdlib ``SIOCSIFFLAGS``; no iproute2);
2. forks a silent child that listens on ``127.0.0.1:<port>`` and forwards bytes
   to the broker's UNIX socket;
3. ``exec``s the harness command so the worker's PID stays the harness.

It holds no token and adds no header: the per-attempt bearer token stays with
the worker as the Pi provider ``apiKey`` and is validated by the broker. It must
never write to stdout, which carries the harness JSONL stream.
"""

from __future__ import annotations

import fcntl
import os
import socket
import struct
import sys
import threading

SIOCGIFFLAGS = 0x8913
SIOCSIFFLAGS = 0x8914
IFF_UP = 0x1
BACKLOG = 32


def loopback_up(ifname: str = "lo") -> bool:
    """Bring ``ifname`` up using the kernel ioctl, without iproute2."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    except OSError:
        return False
    try:
        request = struct.pack("16sH", ifname.encode("utf-8"), 0)
        flags = struct.unpack("16sH", fcntl.ioctl(sock.fileno(), SIOCGIFFLAGS, request))[1]
        if flags & IFF_UP:
            return True
        request = struct.pack("16sH", ifname.encode("utf-8"), flags | IFF_UP)
        fcntl.ioctl(sock.fileno(), SIOCSIFFLAGS, request)
        return True
    except OSError:
        return False
    finally:
        sock.close()


def _pump(source: socket.socket, destination: socket.socket) -> None:
    try:
        while True:
            chunk = source.recv(65536)
            if not chunk:
                break
            destination.sendall(chunk)
    except OSError:
        pass
    finally:
        for endpoint in (source, destination):
            try:
                endpoint.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass


def _serve(socket_path: str, port: int) -> None:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", port))
    listener.listen(BACKLOG)
    while True:
        try:
            client, _ = listener.accept()
        except OSError:
            return
        try:
            upstream = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            upstream.connect(socket_path)
        except OSError:
            client.close()
            continue
        threading.Thread(target=_pump, args=(client, upstream), daemon=True).start()
        threading.Thread(target=_pump, args=(upstream, client), daemon=True).start()


def main() -> None:
    try:
        separator = sys.argv.index("--")
    except ValueError:
        raise SystemExit(2) from None
    socket_path = sys.argv[1]
    port = int(sys.argv[2])
    command = sys.argv[separator + 1 :]
    if not command:
        raise SystemExit(2)
    if not loopback_up():
        # No loopback means no inference path at all; fail before running the
        # harness rather than letting it hang.
        raise SystemExit(3)
    child = os.fork()
    if child == 0:
        # The forked relay must never write to the harness streams (stdout is
        # the JSONL protocol) and must not hold the parent's stdout pipe open.
        try:
            devnull = os.open(os.devnull, os.O_WRONLY)
            os.dup2(devnull, 1)
            os.dup2(devnull, 2)
        except OSError:
            pass
        try:
            _serve(socket_path, port)
        finally:
            os._exit(0)
    os.execvpe(command[0], command, os.environ)


if __name__ == "__main__":
    main()
