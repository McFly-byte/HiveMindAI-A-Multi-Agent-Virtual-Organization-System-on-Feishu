from __future__ import annotations


def should_continue(child_count: int, *, max_child_runs: int) -> bool:
    return child_count < max_child_runs
