#!/usr/bin/env python3
"""Entry point for ``python -m cronwrap`` and console scripts."""
import sys

from cronwrap.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
