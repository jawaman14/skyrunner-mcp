"""Run a command under the machine-wide Godot lock that the MCP server's jobs take.

For Godot runs started from a shell rather than through the server (`bash tools/test.sh`, balance runs), so they
queue behind the other agent's jobs instead of competing with them for memory:

    skyrunner-godot-lock --wait -- bash tools/test.sh docs
"""
import argparse
import os
import subprocess
import sys
import time

from .collaboration import MachineLock, machine_lock_path

BUSY_EXIT = 75  # EX_TEMPFAIL: try again later


def main(argv=None):
    parser = argparse.ArgumentParser(prog="skyrunner-godot-lock", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--wait", action="store_true", help="wait for the lock instead of refusing")
    parser.add_argument("command", nargs=argparse.REMAINDER, help="the command, after --")
    args = parser.parse_args(argv)
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("give the command to run after --")
    try:
        path = machine_lock_path()
    except ValueError as e:
        parser.error(str(e))
    if path is None:
        return subprocess.call(command)
    holder = {"kind": "shell: " + " ".join(command)[:200], "root": os.getcwd(), "pid": os.getpid(),
              "started": time.time()}
    told = False
    while True:
        try:
            lock = MachineLock(path, holder)
            break
        except RuntimeError as e:
            if not args.wait:
                print(e, file=sys.stderr)
                return BUSY_EXIT
            if not told:
                print(f"{e} Waiting for it.", file=sys.stderr, flush=True)
                told = True
            time.sleep(5)
    try:
        return subprocess.call(command)
    finally:
        lock.close()


if __name__ == "__main__":
    raise SystemExit(main())
