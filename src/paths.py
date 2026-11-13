"""Separate working evidence from source files and curated deliverables."""

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
WORK_ROOT = Path(
    os.environ.get(
        "WIRELESS_AGENT_WORK_DIR", str(PROJECT_ROOT.parent / f"{PROJECT_ROOT.name}_开发记录")
    )
)

