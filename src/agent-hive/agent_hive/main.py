from __future__ import annotations

import asyncio

from agent_hive.cli import run_cli


def main() -> None:
    try:
        raise SystemExit(asyncio.run(run_cli()))
    except KeyboardInterrupt:
        raise SystemExit(130) from None


if __name__ == "__main__":
    main()
