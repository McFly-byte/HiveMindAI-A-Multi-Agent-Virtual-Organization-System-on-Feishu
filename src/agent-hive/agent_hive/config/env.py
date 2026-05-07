from __future__ import annotations

import os
from pathlib import Path


def load_dotenv_if_present(project_root: Path, *, override: bool = False) -> None:
    """Load ``<project_root>/.env`` into ``os.environ``.

    This is intentionally small and local to ``agent_hive`` so the new runtime
    does not depend on legacy tool integration code. Existing process env wins
    unless ``override`` is explicitly enabled.
    """

    path = (project_root / ".env").resolve()
    if not path.is_file():
        return
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        return
    for line in lines:
        key, value = _parse_dotenv_line(line)
        if not key:
            continue
        if override or key not in os.environ:
            os.environ[key] = value


def _parse_dotenv_line(line: str) -> tuple[str, str]:
    text = line.strip()
    if not text or text.startswith("#") or "=" not in text:
        return "", ""
    key, _, value = text.partition("=")
    key = key.strip()
    if not key:
        return "", ""
    raw = value.strip()
    if (raw.startswith('"') and raw.endswith('"')) or (raw.startswith("'") and raw.endswith("'")):
        raw = raw[1:-1]
    return key, raw
