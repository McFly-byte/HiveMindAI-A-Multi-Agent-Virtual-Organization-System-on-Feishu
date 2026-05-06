from __future__ import annotations


def has_evidence(payload: dict) -> bool:
    evidence = payload.get("evidence") or payload.get("evidence_refs")
    return bool(evidence)
