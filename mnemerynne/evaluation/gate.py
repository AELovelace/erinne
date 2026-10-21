"""Compare two answer-evaluation reports; decide whether a model may be deployed.

A candidate passes when no quality metric drops more than `tolerance` below the
baseline, faithfulness and refusal F1 are not worse, and answers still parse.
"""

from __future__ import annotations

HIGHER_IS_BETTER = ["answer_rate", "refusal_recall", "refusal_precision", "citation_precision",
                    "faithfulness", "key_fact_coverage", "valid_json"]


def refusal_f1(summary: dict) -> float | None:
    p, r = summary.get("refusal_precision"), summary.get("refusal_recall")
    if p is None or r is None or p + r == 0:
        return None
    return round(2 * p * r / (p + r), 3)


def compare(baseline: dict, candidate: dict, tolerance: float = 0.02) -> tuple[bool, list[str]]:
    base, cand = baseline["summary"], candidate["summary"]
    lines, ok = [], True
    for key in HIGHER_IS_BETTER + ["refusal_f1", "total_p50_ms"]:
        b = refusal_f1(base) if key == "refusal_f1" else base.get(key)
        c = refusal_f1(cand) if key == "refusal_f1" else cand.get(key)
        if b is None or c is None:
            lines.append(f"{key:>20}: {b} -> {c}")
            continue
        delta = c - b
        verdict = ""
        if key == "total_p50_ms":
            verdict = "(info)"
        elif key in ("faithfulness", "refusal_f1") and delta < 0:
            verdict, ok = "FAIL: must not get worse", False
        elif delta < -tolerance:
            verdict, ok = f"FAIL: dropped more than {tolerance}", False
        lines.append(f"{key:>20}: {b} -> {c} ({delta:+.3f}) {verdict}".rstrip())
    if (cand.get("valid_json") or 0) < 0.98:
        ok = False
        lines.append("FAIL: under 98% of answers were valid JSON")
    prompt_b = baseline.get("config", {}).get("prompt_version")
    prompt_c = candidate.get("config", {}).get("prompt_version")
    if prompt_b != prompt_c:
        ok = False
        lines.append(f"FAIL: reports use different prompt versions ({prompt_b} vs {prompt_c}); re-run both")
    return ok, lines
