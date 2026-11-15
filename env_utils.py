"""
Environment file helpers.
"""

from __future__ import annotations

import os
import re
from pathlib import Path


_ENV_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def get_env_file(default_dir: str | Path) -> Path:
    return Path(os.environ.get("BILISUMMARY_ENV_FILE", Path(default_dir) / ".env.local"))


def quote_env_value(value: str) -> str:
    escaped = str(value or "")
    escaped = escaped.replace("\\", "\\\\")
    escaped = escaped.replace("\"", "\\\"")
    escaped = escaped.replace("\n", "\\n").replace("\r", "\\r")
    return f"\"{escaped}\""


def set_env_key(dotenv_path: str | Path, key: str, value: str):
    """Update a dotenv key without replacing the target file.

    python-dotenv rewrites files via os.replace(), which fails for Docker
    single-file bind mounts. This writer keeps the mounted inode and truncates
    the file in place so host-visible config files stay synchronized.
    """

    if not _ENV_KEY_RE.match(key):
        raise ValueError(f"Invalid environment key: {key}")

    path = Path(dotenv_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.touch()

    encoded = f"{key}={quote_env_value(value)}\n"
    lines = path.read_text(encoding="utf-8", errors="ignore").splitlines(keepends=True)
    replaced = False
    output: list[str] = []

    for line in lines:
        stripped = line.lstrip()
        if stripped.startswith("#") or "=" not in stripped:
            output.append(line)
            continue

        candidate = stripped.split("=", 1)[0].strip()
        if candidate == key:
            output.append(encoded)
            replaced = True
        else:
            output.append(line)

    if not replaced:
        if output and not output[-1].endswith(("\n", "\r")):
            output[-1] = output[-1] + "\n"
        output.append(encoded)

    with path.open("r+", encoding="utf-8") as file:
        file.write("".join(output))
        file.truncate()
