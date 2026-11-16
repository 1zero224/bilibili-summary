"""Environment file helpers."""

from __future__ import annotations

import os
from pathlib import Path


def get_env_file(default_dir: str | Path) -> Path:
    return Path(os.environ.get("BILISUMMARY_ENV_FILE", Path(default_dir) / ".env.local"))
