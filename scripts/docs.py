#!/usr/bin/env python3
"""Query the documentation index (thin wrapper; no install needed).

    python3 scripts/docs.py find phase:P2
    python3 scripts/docs.py context phase:P2
    python3 scripts/docs.py show verifier.sims

The installed equivalent is the ``adb-docs`` console script.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from adb_docs.cli import main  # noqa: E402

raise SystemExit(main())
