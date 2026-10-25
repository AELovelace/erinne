"""Compare two answer-evaluation reports; decide whether a model may be deployed.

A candidate passes when no quality metric drops more than `tolerance` below the
baseline, faithfulness and refusal F1 are not worse, and answers still parse.
Reports of the agent path (eval-answers --agent, summary "path": "agent") are
only compared with each other; they have no JSON to parse, and show the
agent's tool calls and fallbacks for information.
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
    path_b, path_c = base.get("path", "answer"), cand.get("path", "answer")
    if path_b != path_c:
        return False, [f"FAIL: reports measure different paths ({path_b} vs {path_c}); re-run both the same way"]
    agent = path_c == "agent"
    keys = [k for k in HIGHER_IS_BETTER if not (agent and k == "valid_json")]
    info = ["total_p50_ms"] + (["fallback_rate", "tool_calls_mean"] if agent else [])
    for key in keys + ["refusal_f1"] + info:
        b = refusal_f1(base) if key == "refusal_f1" else base.get(key)
        c = refusal_f1(cand) if key == "refusal_f1" else cand.get(key)
        if b is None or c is None:
            lines.append(f"{key:>20}: {b} -> {c}")
            continue
        delta = c - b
        verdict = ""
        if key in info:
            verdict = "(info)"
        elif key in ("faithfulness", "refusal_f1") and delta < 0:
            verdict, ok = "FAIL: must not get worse", False
        elif delta < -tolerance:
            verdict, ok = f"FAIL: dropped more than {tolerance}", False
        lines.append(f"{key:>20}: {b} -> {c} ({delta:+.3f}) {verdict}".rstrip())
    if not agent and (cand.get("valid_json") or 0) < 0.98:
        ok = False
        lines.append("FAIL: under 98% of answers were valid JSON")
    prompt_b = baseline.get("config", {}).get("prompt_version")
    prompt_c = candidate.get("config", {}).get("prompt_version")
    if prompt_b != prompt_c:
        ok = False
        lines.append(f"FAIL: reports use different prompt versions ({prompt_b} vs {prompt_c}); re-run both")
    return ok, lines
