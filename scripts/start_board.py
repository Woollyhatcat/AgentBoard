"""Ensure the local board is visible, including from an unrelated working directory."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agentboard.__main__ import main

if __name__ == "__main__":
    sys.argv.insert(1, "start")
    main()
