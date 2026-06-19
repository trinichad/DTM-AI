"""Enables `python3 -m execution.web`."""
import sys

from .server import main

if __name__ == "__main__":
    sys.exit(main())
