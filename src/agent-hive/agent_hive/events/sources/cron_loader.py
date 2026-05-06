from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, ValidationError


class CronJobConfig(BaseModel):
    name: str
    interval_seconds: float
    event_type: str
    target_agent_id: str | None = "orchestrator"
    payload: dict[str, Any] = Field(default_factory=dict)
    run_on_start: bool = True


class CronConfigError(ValueError):
    pass


def _read_yaml(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise CronConfigError(f"cron YAML root must be a mapping: {path}")
    return data


def load_cron_jobs(cron_dir: Path | str) -> list[CronJobConfig]:
    base = Path(cron_dir)
    if not base.exists():
        return []

    jobs: list[CronJobConfig] = []
    for path in sorted([*base.glob("*.yaml"), *base.glob("*.yml")]):
        raw = _read_yaml(path)
        items = raw.get("jobs")
        if not isinstance(items, list):
            raise CronConfigError(f"cron file missing jobs list: {path}")
        for idx, item in enumerate(items):
            if not isinstance(item, dict):
                raise CronConfigError(f"cron job must be mapping: {path}#{idx}")
            payload = dict(item)
            payload.setdefault("name", f"{path.stem}_{idx + 1}")
            try:
                jobs.append(CronJobConfig.model_validate(payload))
            except ValidationError as exc:
                raise CronConfigError(f"invalid cron job: {path}#{idx}") from exc
    return jobs
