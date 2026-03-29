from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from ..normalization import build_query_profile
from ..retrieval import MatchDecisionPolicy, RankedCandidate
from .hard_negatives import NEGATIVE_HARD_NEGATIVE_CASES, POSITIVE_HARD_NEGATIVE_CASES
from .real_world_eval import NEGATIVE_REAL_WORLD_CASES, POSITIVE_REAL_WORLD_CASES


@dataclass(frozen=True, slots=True)
class CalibrationEvalCase:
    slug: str
    mode: str
    error_family: str
    expected_title: str | None
    safe_statuses: tuple[str, ...]
    ranked: list[RankedCandidate]


_GRID_ACCEPT = (0.60, 0.64, 0.68, 0.72, 0.76)
_GRID_WEAK = (0.32, 0.36, 0.40, 0.44, 0.48)
_GRID_MARGIN = (0.06, 0.09, 0.12, 0.15)


def _top_title(ranked: list[RankedCandidate]) -> str:
    if not ranked:
        return ""
    candidate = ranked[0].candidate
    best_variant = candidate.get("best_variant") or {}
    return str(best_variant.get("title") or candidate.get("title") or "")


def _precompute_cases(app: Any, *, limit: int = 3) -> list[CalibrationEvalCase]:
    cases: list[CalibrationEvalCase] = []
    for raw_case in list(POSITIVE_REAL_WORLD_CASES) + list(NEGATIVE_REAL_WORLD_CASES) + list(POSITIVE_HARD_NEGATIVE_CASES) + list(NEGATIVE_HARD_NEGATIVE_CASES):
        profile = build_query_profile(
            error_text=raw_case.error_text,
            command=raw_case.command,
            file_path=raw_case.file_path,
            repo_name=raw_case.repo_name,
            project_scope=raw_case.project_scope,
        )
        ranked = app.matcher.ranked_candidates(
            profile,
            project_scope=raw_case.project_scope,
            limit=limit,
            repo_name=raw_case.repo_name,
            retrieval_context="match",
        )
        expected_title = getattr(raw_case, "expected_title", None)
        expected_status = getattr(raw_case, "expected_status", None)
        if raw_case.mode == "positive":
            safe_statuses = ("match", "ambiguous")
        elif expected_status == "ambiguous":
            safe_statuses = ("ambiguous", "abstain")
        else:
            safe_statuses = (str(expected_status or "abstain"),)
        cases.append(
            CalibrationEvalCase(
                slug=raw_case.slug,
                mode=raw_case.mode,
                error_family=str(getattr(raw_case, "error_family", "") or profile.error_family or "unknown"),
                expected_title=expected_title,
                safe_statuses=safe_statuses,
                ranked=ranked,
            )
        )
    return cases


def _evaluate_case_set(
    cases: list[CalibrationEvalCase],
    *,
    accept_threshold: float,
    weak_threshold: float,
    ambiguity_margin: float,
) -> dict[str, Any]:
    policy = MatchDecisionPolicy(
        accept_threshold=accept_threshold,
        weak_threshold=weak_threshold,
        ambiguity_margin=ambiguity_margin,
    )
    positive_total = 0
    positive_actionable = 0
    positive_top1 = 0
    clear_match_total = 0
    clear_match_correct = 0
    negative_total = 0
    negative_safe = 0
    clear_false_positive = 0

    for case in cases:
        decision = policy.decide(case.ranked)
        top_title = _top_title(case.ranked)
        if case.mode == "positive":
            positive_total += 1
            actionable = decision.status in {"match", "ambiguous"}
            if actionable:
                positive_actionable += 1
            if actionable and top_title == case.expected_title:
                positive_top1 += 1
            if decision.status == "match":
                clear_match_total += 1
                if top_title == case.expected_title:
                    clear_match_correct += 1
                else:
                    clear_false_positive += 1
        else:
            negative_total += 1
            if decision.status in case.safe_statuses:
                negative_safe += 1
            elif decision.status == "match":
                clear_false_positive += 1

    top1_accuracy = positive_top1 / max(positive_total, 1)
    actionable_rate = positive_actionable / max(positive_total, 1)
    clear_match_precision = clear_match_correct / max(clear_match_total, 1)
    negative_safety_rate = negative_safe / max(negative_total, 1)
    false_positive_rate = clear_false_positive / max(len(cases), 1)
    objective = (
        3.0 * top1_accuracy
        + 2.3 * negative_safety_rate
        + 1.8 * clear_match_precision
        + 0.5 * actionable_rate
        - 3.2 * false_positive_rate
    )
    return {
        "accept_threshold": round(accept_threshold, 4),
        "weak_threshold": round(weak_threshold, 4),
        "ambiguity_margin": round(ambiguity_margin, 4),
        "top1_accuracy": round(top1_accuracy, 4),
        "actionable_rate": round(actionable_rate, 4),
        "clear_match_precision": round(clear_match_precision, 4),
        "negative_safety_rate": round(negative_safety_rate, 4),
        "false_positive_rate": round(false_positive_rate, 4),
        "objective": round(objective, 6),
        "case_count": len(cases),
    }


def _search_best(cases: list[CalibrationEvalCase]) -> dict[str, Any]:
    best: dict[str, Any] | None = None
    for accept_threshold in _GRID_ACCEPT:
        for weak_threshold in _GRID_WEAK:
            if weak_threshold >= accept_threshold:
                continue
            for ambiguity_margin in _GRID_MARGIN:
                candidate = _evaluate_case_set(
                    cases,
                    accept_threshold=accept_threshold,
                    weak_threshold=weak_threshold,
                    ambiguity_margin=ambiguity_margin,
                )
                if best is None or tuple(
                    candidate[key] for key in ("objective", "negative_safety_rate", "clear_match_precision", "top1_accuracy")
                ) > tuple(
                    best[key] for key in ("objective", "negative_safety_rate", "clear_match_precision", "top1_accuracy")
                ):
                    best = candidate
    assert best is not None
    return best


def run_threshold_calibration(app: Any) -> dict[str, Any]:
    cases = _precompute_cases(app, limit=3)
    global_best = _search_best(cases)
    families: dict[str, dict[str, Any]] = {}
    counts: dict[str, int] = {}
    for case in cases:
        if case.mode != "positive":
            continue
        counts[case.error_family] = counts.get(case.error_family, 0) + 1
    for family, count in sorted(counts.items()):
        if count < 2:
            continue
        family_cases = [case for case in cases if case.mode == "negative" or case.error_family == family]
        families[family] = _search_best(family_cases)

    return {
        "version": 1,
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "global": {
            "accept_threshold": global_best["accept_threshold"],
            "weak_threshold": global_best["weak_threshold"],
            "ambiguity_margin": global_best["ambiguity_margin"],
        },
        "families": {
            family: {
                "accept_threshold": report["accept_threshold"],
                "weak_threshold": report["weak_threshold"],
                "ambiguity_margin": report["ambiguity_margin"],
            }
            for family, report in families.items()
        },
        "metrics": {
            "global": global_best,
            "families": families,
            "case_count": len(cases),
        },
        "datasets": {
            "real_world_positive": len(POSITIVE_REAL_WORLD_CASES),
            "real_world_negative": len(NEGATIVE_REAL_WORLD_CASES),
            "hard_negative_positive": len(POSITIVE_HARD_NEGATIVE_CASES),
            "hard_negative_negative": len(NEGATIVE_HARD_NEGATIVE_CASES),
        },
    }


__all__ = ["run_threshold_calibration"]
