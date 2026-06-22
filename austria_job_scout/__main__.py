"""Entry point so `python -m austria_job_scout ...` works."""
from __future__ import annotations

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
