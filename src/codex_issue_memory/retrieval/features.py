from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable

from ..models import QueryProfile
from ..normalization import compare_entity_slots, tokenize


def _token_overlap_score(left: list[str], right: list[str]) -> tuple[float, list[str]]:
    if not left or not right:
        return 0.0, []
    lset = set(left)
    rset = set(right)
    overlap = sorted(lset & rset)
    if not overlap:
        return 0.0, []
    union = lset | rset
    return len(overlap) / max(1, len(union)), overlap[:6]


def _safe_dt(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _clamp(value: float, *, low: float = 0.0, high: float = 1.0) -> float:
    return min(max(float(value), low), high)


def scope_signal(project_scope: str, candidate_scope: str) -> tuple[float, str | None]:
    if project_scope and project_scope != "global":
        if candidate_scope == project_scope:
            return 1.0, "same-project-scope"
        if candidate_scope == "global":
            return 0.45, "global-fallback"
        return 0.0, None
    if project_scope == "global":
        if candidate_scope == "global":
            return 0.70, "global-scope"
        return 0.0, None
    if candidate_scope == "global":
        return 0.30, "global-scope"
    return 0.20, "scope-unspecified"


def recency_signal(updated_at: str) -> float:
    dt = _safe_dt(updated_at)
    if dt is None:
        return 0.0
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    age_days = max((datetime.now(timezone.utc) - dt).total_seconds() / 86400.0, 0.0)
    return 1.0 / (1.0 + age_days / 30.0)


def lexical_signal(retrieval_signals: dict[str, Any]) -> float:
    scores: list[float] = []
    variant_rank = retrieval_signals.get("variant_fts_rank")
    episode_rank = retrieval_signals.get("episode_fts_rank")
    pattern_rank = retrieval_signals.get("pattern_fts_rank")
    example_rank = retrieval_signals.get("example_fts_rank")
    if isinstance(variant_rank, int) and variant_rank > 0:
        scores.append(1.10 / variant_rank)
    if isinstance(episode_rank, int) and episode_rank > 0:
        scores.append(1.00 / episode_rank)
    if isinstance(pattern_rank, int) and pattern_rank > 0:
        scores.append(0.85 / pattern_rank)
    if isinstance(example_rank, int) and example_rank > 0:
        scores.append(0.75 / example_rank)
    if retrieval_signals.get("root_rank") is not None:
        scores.append(0.78)
    if retrieval_signals.get("family_rank") is not None:
        scores.append(0.58)
    return max(scores) if scores else 0.0


def _best_row_overlap(
    profile_tokens: list[str],
    rows: Iterable[dict[str, Any]],
    fields: tuple[str, ...],
    *,
    max_tokens: int,
) -> tuple[float, list[str]]:
    best_score = 0.0
    best_tokens: list[str] = []
    for row in rows:
        row_tokens = tokenize(" ".join(str(row.get(field, "")) for field in fields), max_tokens=max_tokens)
        overlap_score, overlap_tokens = _token_overlap_score(profile_tokens, row_tokens)
        if overlap_score > best_score:
            best_score = overlap_score
            best_tokens = overlap_tokens[:4]
    return best_score, best_tokens


def _prioritize_reasons(reasons: list[str], *, limit: int = 12) -> list[str]:
    priority_prefixes = (
        "same-root-cause",
        "same-error-family",
        "strategy-bandit-safe-override",
        "strategy-bandit-exploitation",
        "strategy-bandit-variant-residual",
        "preference-rule",
        "avoidance-rule",
        "strategy-preference",
        "dense-retrieval",
        "context-variant-match",
        "session-rejection-memory",
        "session-acceptance-memory",
        "variant-stack-match",
        "variant-command-match",
        "variant-path-match",
        "retrieval-signal",
        "feedback-prior",
        "prior-success",
        "proven-variant",
    )
    prioritized: list[str] = []
    seen: set[str] = set()

    for prefix in priority_prefixes:
        for reason in reasons:
            if reason in seen:
                continue
            if reason == prefix or reason.startswith(prefix + ":"):
                prioritized.append(reason)
                seen.add(reason)
                break

    for reason in reasons:
        if reason in seen:
            continue
        prioritized.append(reason)
        seen.add(reason)

    return prioritized[:limit]

def _feedback_score(candidate: dict[str, Any], variant: dict[str, Any]) -> float:
    variant_success = int(variant.get("success_count", 0))
    variant_reject = int(variant.get("reject_count", 0))
    confidence = float(variant.get("confidence", candidate.get("confidence", 0.5)))
    memory_strength = float(variant.get("memory_strength", 0.5))
    success_ratio = (variant_success + 1.0) / max(variant_success + variant_reject + 2.0, 1.0)
    reject_ratio = variant_reject / max(variant_success + variant_reject + 1.0, 1.0)
    return _clamp(0.42 * confidence + 0.33 * memory_strength + 0.25 * success_ratio - 0.22 * reject_ratio)


def build_candidate_features(
    profile: QueryProfile,
    candidate: dict[str, Any],
    *,
    project_scope: str,
    entity_importance: dict[str, float] | None = None,
) -> tuple[dict[str, float], list[str]]:
    reasons: list[str] = []

    candidate_scope = str(candidate.get("project_scope", ""))
    scope_score, scope_reason = scope_signal(project_scope, candidate_scope)
    if scope_reason:
        reasons.append(scope_reason)

    family_score = 0.0
    if profile.error_family != "generic_runtime_error" and candidate.get("error_family") == profile.error_family:
        family_score = 1.0
        reasons.append("same-error-family")

    root_score = 0.0
    if profile.root_cause_class != "unknown" and candidate.get("root_cause_class") == profile.root_cause_class:
        root_score = 1.0
        reasons.append("same-root-cause")

    variant = candidate.get("best_variant") or {}
    if not isinstance(variant, dict):
        variant = {}

    raw_variant_tags = variant.get("tags_json")
    variant_tags = [str(tag) for tag in raw_variant_tags] if isinstance(raw_variant_tags, list) else []
    candidate_tags = tokenize(
        str(candidate.get("tags", "")) + " " + " ".join(variant_tags),
        max_tokens=32,
    )
    candidate_text_tokens = tokenize(
        " ".join(
            [
                str(candidate.get("title", "")),
                str(candidate.get("canonical_symptom", "")),
                str(candidate.get("canonical_fix", "")),
                str(candidate.get("prevention_rule", "")),
                str(candidate.get("verification_steps", "")),
                str(candidate.get("tags", "")),
                str(variant.get("title", "")),
                str(variant.get("canonical_fix", "")),
                str(variant.get("verification_steps", "")),
                str(variant.get("patch_summary", "")),
                " ".join(str(tag) for tag in variant_tags),
            ]
        ),
        max_tokens=160,
    )

    text_overlap, text_tokens = _token_overlap_score(profile.tokens, candidate_text_tokens)
    if text_overlap > 0:
        reasons.append("text-overlap:" + ",".join(text_tokens[:4]))

    tag_overlap, tag_tokens = _token_overlap_score(profile.tags, candidate_tags)
    if tag_overlap > 0:
        reasons.append("tag-overlap:" + ",".join(tag_tokens[:4]))

    exception_overlap, exception_tokens = _token_overlap_score(profile.exception_types, candidate_text_tokens + candidate_tags)
    if exception_overlap > 0:
        reasons.append("exception-overlap:" + ",".join(exception_tokens[:3]))

    examples = candidate.get("examples", []) if isinstance(candidate.get("examples"), list) else []
    episodes = candidate.get("episodes", []) if isinstance(candidate.get("episodes"), list) else []

    best_example_overlap, best_example_tokens = _best_row_overlap(
        profile.tokens,
        examples,
        ("raw_error", "normalized_error", "context", "command", "file_path", "verified_fix"),
        max_tokens=72,
    )
    if best_example_overlap > 0:
        reasons.append("example-overlap:" + ",".join(best_example_tokens))

    best_episode_overlap, best_episode_tokens = _best_row_overlap(
        profile.tokens,
        episodes,
        ("raw_error", "normalized_error", "context", "stack_excerpt", "command", "file_path", "patch_summary", "resolution_notes"),
        max_tokens=88,
    )
    if best_episode_overlap > 0:
        reasons.append("episode-overlap:" + ",".join(best_episode_tokens))

    best_command_overlap, best_command_tokens = _best_row_overlap(profile.command_tokens, examples, ("command",), max_tokens=24)
    episode_command_overlap, episode_command_tokens = _best_row_overlap(profile.command_tokens, episodes, ("command",), max_tokens=24)
    if episode_command_overlap > best_command_overlap:
        best_command_overlap, best_command_tokens = episode_command_overlap, episode_command_tokens

    best_path_overlap, best_path_tokens = _best_row_overlap(profile.path_tokens, examples, ("file_path",), max_tokens=24)
    episode_path_overlap, episode_path_tokens = _best_row_overlap(profile.path_tokens, episodes, ("file_path",), max_tokens=24)
    if episode_path_overlap > best_path_overlap:
        best_path_overlap, best_path_tokens = episode_path_overlap, episode_path_tokens

    variant_command_overlap = 1.0 if profile.command_signature and variant.get("command_signature") == profile.command_signature else 0.0
    variant_path_overlap = 1.0 if profile.path_signature and variant.get("file_path_signature") == profile.path_signature else 0.0
    variant_stack_overlap = 1.0 if profile.stack_signature and variant.get("stack_signature") == profile.stack_signature else 0.0
    if variant_command_overlap > 0:
        reasons.append("variant-command-match")
    if variant_path_overlap > 0:
        reasons.append("variant-path-match")
    if variant_stack_overlap > 0:
        reasons.append("variant-stack-match")
    if max(best_command_overlap, variant_command_overlap) > 0 and best_command_tokens:
        reasons.append("command-overlap:" + ",".join(best_command_tokens))
    if max(best_path_overlap, variant_path_overlap) > 0 and best_path_tokens:
        reasons.append("path-overlap:" + ",".join(best_path_tokens))

    env_score = 0.0
    if profile.env_fingerprint and variant.get("env_fingerprint") == profile.env_fingerprint:
        env_score += 0.55
        reasons.append("variant-env-match")
    if profile.repo_fingerprint and variant.get("repo_fingerprint") == profile.repo_fingerprint:
        env_score += 0.45
        reasons.append("variant-repo-match")
    raw_applicability = variant.get("applicability_json")
    applicability = raw_applicability if isinstance(raw_applicability, dict) else {}
    if profile.repo_name and applicability.get("repo_name") == profile.repo_name:
        env_score += 0.45
        reasons.append("variant-repo-name-match")

    raw_entity_slots = variant.get("entity_slots_json")
    entity_slots = raw_entity_slots if isinstance(raw_entity_slots, dict) else {}
    entity_signals = compare_entity_slots(profile.entity_slots, entity_slots)
    entity_match_score = float(entity_signals.get("match_score", 0.0))
    entity_conflict_penalty_score = float(entity_signals.get("conflict_penalty", 0.0))

    # Phase 4.3: Scale entity scores by learned importance weights
    if entity_importance:
        involved_keys = list(entity_signals.get("matched_keys", [])) + list(entity_signals.get("conflict_keys", []))
        if involved_keys:
            weights = [entity_importance.get(k, 1.0) for k in involved_keys]
            avg_weight = sum(weights) / len(weights)
            entity_match_score *= avg_weight
            entity_conflict_penalty_score *= avg_weight

    for reason in entity_signals.get("reasons", []):
        if reason not in reasons:
            reasons.append(str(reason))

    retrieval_signals = dict(candidate.get("retrieval_signals", {}))
    lexical_score = lexical_signal(retrieval_signals)
    if lexical_score > 0:
        reasons.append("retrieval-signal")

    dense_score = _clamp(max(float(candidate.get("dense_score", 0.0)), float(retrieval_signals.get("dense_score", 0.0))))
    if dense_score > 0.0:
        reasons.append("dense-retrieval")

    variant_score = _clamp(max(float(candidate.get("variant_match_score", 0.0)), variant_stack_overlap * 0.35))
    if variant_score > 0.45:
        reasons.append("context-variant-match")

    support_value = max(int(variant.get("times_used", 0)), int(candidate.get("times_seen", 1)))
    support_score = min(support_value, 10) / 10.0
    recency_score = max(
        recency_signal(str(variant.get("updated_at", ""))),
        recency_signal(str(candidate.get("updated_at", ""))),
    )
    feedback_score = _feedback_score(candidate, variant)
    if feedback_score > 0.55:
        reasons.append("feedback-prior")

    success_prior_score = _clamp(max(float(variant.get("confidence", candidate.get("confidence", 0.5))), feedback_score))
    if success_prior_score > 0.55:
        reasons.append("prior-success")

    variant_success = int(variant.get("success_count", 0))
    variant_reject = int(variant.get("reject_count", 0))
    times_used = max(int(variant.get("times_used", 0)), int(candidate.get("times_seen", 0)))
    proven_score = (variant_success + 1) / (times_used + 2) if (variant_success + variant_reject) > 0 else 0.0
    if proven_score > 0.55:
        reasons.append("proven-variant")

    session_penalty_score = _clamp(float(candidate.get("session_penalty", 0.0)))
    session_boost_score = _clamp(float(candidate.get("session_boost", 0.0)))
    if session_penalty_score > 0:
        reasons.append("session-rejection-memory")
    if session_boost_score > 0:
        reasons.append("session-acceptance-memory")

    features = {
        "scope_score": scope_score,
        "family_score": family_score,
        "root_score": root_score,
        "lexical_score": lexical_score,
        "dense_score": dense_score,
        "text_overlap_score": text_overlap,
        "tag_overlap_score": tag_overlap,
        "exception_overlap_score": exception_overlap,
        "example_score": best_example_overlap,
        "episode_score": best_episode_overlap,
        "command_score": max(best_command_overlap, variant_command_overlap),
        "path_score": max(best_path_overlap, variant_path_overlap),
        "env_score": _clamp(env_score),
        "entity_match_score": entity_match_score,
        "entity_conflict_penalty_score": entity_conflict_penalty_score,
        "variant_score": variant_score,
        "support_score": support_score,
        "recency_score": recency_score,
        "feedback_score": feedback_score,
        "success_prior_score": success_prior_score,
        "proven_score": proven_score,
        "session_penalty_score": session_penalty_score,
        "session_boost_score": session_boost_score,
    }
    return features, _prioritize_reasons(reasons, limit=12)
