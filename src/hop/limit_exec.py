"""Apply mandatory resource limits in a fresh process before exec, never after fork."""

import os
import resource
import sys


def main() -> None:
    cpu, processes, memory = (int(value) for value in sys.argv[1:4])
    for kind, value in (
        (resource.RLIMIT_CPU, cpu),
        (resource.RLIMIT_NPROC, processes),
        (resource.RLIMIT_AS, memory),
    ):
        resource.setrlimit(kind, (value, value))
    command = sys.argv[4:]
    os.execvpe(command[0], command, os.environ)


if __name__ == "__main__":
    main()
