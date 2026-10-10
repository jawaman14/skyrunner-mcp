"""Portable Godot test worker; imports must succeed before tests start."""
import argparse
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("godot")
    parser.add_argument("--reimport", action="store_true")
    parser.add_argument("--filter", default="")
    args = parser.parse_args()
    if args.reimport:
        print("== import", flush=True)
        result = subprocess.run([args.godot, "--headless", "--import"],
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace")
        if result.returncode or "SCRIPT ERROR" in result.stdout or "Parse Error" in result.stdout:
            print(result.stdout, flush=True)
            print("FAILED: import did not complete cleanly", flush=True)
            return result.returncode or 100
    print("== tests", flush=True)
    command = [args.godot, "--headless", "--script", "res://tests/run_tests.gd"]
    if args.filter:
        command += ["--", args.filter]
    return subprocess.call(command)


if __name__ == "__main__":
    sys.exit(main())
