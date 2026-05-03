from __future__ import annotations

import argparse
import json
from pathlib import Path

from pmo_mvp.demo_data import build_demo_state
from pmo_mvp.engine import PMOCycleEngine
from pmo_mvp.store import JsonStateStore


ROOT = Path(__file__).resolve().parent
RUNTIME_PATH = ROOT / "runtime" / "state.json"
OUTPUT_DIR = ROOT / "output"


def init_demo() -> None:
    store = JsonStateStore(RUNTIME_PATH)
    store.save(build_demo_state())
    print(f"demo state initialized at: {RUNTIME_PATH}")


def run_cycle() -> None:
    store = JsonStateStore(RUNTIME_PATH)
    if not RUNTIME_PATH.exists():
        store.save(build_demo_state())
    engine = PMOCycleEngine(store=store, output_dir=OUTPUT_DIR)
    summary = engine.run()
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def show_state() -> None:
    if not RUNTIME_PATH.exists():
        raise SystemExit("state file not found, run `python3 run_demo.py init-demo` first")
    data = json.loads(RUNTIME_PATH.read_text(encoding="utf-8"))
    print(json.dumps(data, ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the PMO multi-agent MVP demo.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("init-demo", help="Create demo runtime data.")
    subparsers.add_parser("run-cycle", help="Run one PMO agent cycle.")
    subparsers.add_parser("show-state", help="Print current runtime state.")
    args = parser.parse_args()

    if args.command == "init-demo":
        init_demo()
    elif args.command == "run-cycle":
        run_cycle()
    elif args.command == "show-state":
        show_state()


if __name__ == "__main__":
    main()

