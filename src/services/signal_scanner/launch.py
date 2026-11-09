"""Compatibility launcher: the scanner now shares DSA's server lifecycle."""

import runpy
import sys


def main():
    sys.argv = [sys.argv[0], "--serve-only", *sys.argv[1:]]
    runpy.run_module("main", run_name="__main__")


if __name__ == "__main__":
    main()
