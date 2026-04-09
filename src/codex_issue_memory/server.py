from __future__ import annotations

from functools import lru_cache
import sys
from typing import Any, Callable, TypeVar

try:
    from mcp.server.fastmcp import FastMCP  # pyright: ignore[reportMissingImports,reportAssignmentType]
except ImportError:  # pragma: no cover - exercised in lightweight test environments
    F = TypeVar("F", bound=Callable[..., Any])

    class FastMCP:  # type: ignore[override]
        """Small import-time shim so lifecycle tests can load the server module without MCP installed."""

        def __init__(self, _name: str, json_response: bool = True) -> None:
            self.json_response = json_response

        def tool(self) -> Callable[[F], F]:
            def decorator(func: F) -> F:
                return func

            return decorator

        def run(self) -> None:
            raise RuntimeError("The 'mcp' package is required to run the issue-memory MCP server.")

from .app import IssueMemoryApp
from .lifecycle import MCPServerLifecycle, MCPServerOwnerConflict
from .settings import Settings

mcp = FastMCP("issue-memory", json_response=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings.from_env()


@lru_cache(maxsize=1)
def get_lifecycle() -> MCPServerLifecycle:
    return MCPServerLifecycle(get_settings())


@lru_cache(maxsize=1)
def get_app() -> IssueMemoryApp:
    app = IssueMemoryApp()
    get_lifecycle().mark_initialized()
    return app


@mcp.tool()
def issue_match(
    error_text: str,
    context: str = "",
    command: str = "",
    file_path: str = "",
    stack_excerpt: str = "",
    env_json: str = "",
    repo_name: str = "",
    git_commit: str = "",
    session_id: str = "",
    project_scope: str = "global",
    user_scope: str = "",
    limit: int = 3,
) -> dict:
    """Find the most similar known issue patterns for a failure."""
    return get_app().issue_match(
        error_text=error_text,
        context=context,
        command=command,
        file_path=file_path,
        stack_excerpt=stack_excerpt,
        env_json=env_json,
        repo_name=repo_name,
        git_commit=git_commit,
        session_id=session_id,
        project_scope=project_scope,
        user_scope=user_scope,
        limit=limit,
    )


@mcp.tool()
def issue_record_resolution(
    title: str,
    raw_error: str,
    canonical_fix: str,
    prevention_rule: str,
    project_scope: str = "global",
    user_scope: str = "",
    canonical_symptom: str = "",
    verification_steps: str = "",
    tags: str = "",
    error_family: str = "auto",
    root_cause_class: str = "auto",
    context: str = "",
    file_path: str = "",
    command: str = "",
    domain: str = "generic",
    stack_excerpt: str = "",
    env_json: str = "",
    repo_name: str = "",
    git_commit: str = "",
    session_id: str = "",
    verification_command: str = "",
    verification_output: str = "",
    resolution_notes: str = "",
    patch_summary: str = "",
    patch_hash: str = "",
) -> dict:
    """Store a verified reusable fix as pattern + variant + episode."""
    return get_app().issue_record_resolution(
        title=title,
        raw_error=raw_error,
        canonical_fix=canonical_fix,
        prevention_rule=prevention_rule,
        project_scope=project_scope,
        user_scope=user_scope,
        canonical_symptom=canonical_symptom,
        verification_steps=verification_steps,
        tags=tags,
        error_family=error_family,
        root_cause_class=root_cause_class,
        context=context,
        file_path=file_path,
        command=command,
        domain=domain,
        stack_excerpt=stack_excerpt,
        env_json=env_json,
        repo_name=repo_name,
        git_commit=git_commit,
        session_id=session_id,
        verification_command=verification_command,
        verification_output=verification_output,
        resolution_notes=resolution_notes,
        patch_summary=patch_summary,
        patch_hash=patch_hash,
    )


@mcp.tool()
def issue_feedback(
    retrieval_event_id: int,
    feedback_type: str,
    retrieval_candidate_id: int = 0,
    candidate_rank: int = 0,
    pattern_id: int = 0,
    variant_id: int = 0,
    actor: str = "user",
    reward: float | None = None,
    notes: str = "",
) -> dict:
    """Record retrieval feedback so the matcher can learn from accepted and rejected candidates."""
    return get_app().issue_feedback(
        retrieval_event_id=retrieval_event_id,
        feedback_type=feedback_type,
        retrieval_candidate_id=retrieval_candidate_id,
        candidate_rank=candidate_rank,
        pattern_id=pattern_id,
        variant_id=variant_id,
        actor=actor,
        reward=reward,
        notes=notes,
    )


@mcp.tool()
def issue_set_preference(
    instruction: str,
    project_scope: str = "global",
    user_scope: str = "",
    repo_name: str = "",
    error_family: str = "auto",
    strategy_key: str = "auto",
    command: str = "",
    file_path: str = "",
    mode: str = "prefer",
    weight: float | None = None,
    source: str = "user_prompt",
) -> dict:
    """Store a prompt-driven strategy preference without duplicating pattern memory."""
    return get_app().issue_set_preference(
        instruction=instruction,
        project_scope=project_scope,
        user_scope=user_scope,
        repo_name=repo_name,
        error_family=error_family,
        strategy_key=strategy_key,
        command=command,
        file_path=file_path,
        mode=mode,
        weight=weight,
        source=source,
    )


@mcp.tool()
def issue_list_preferences(
    scope_type: str = "",
    scope_key: str = "",
    project_scope: str = "",
    repo_name: str = "",
    active_only: bool = True,
    limit: int = 20,
) -> dict:
    """List stored prompt-driven preference rules."""
    return get_app().issue_list_preferences(
        scope_type=scope_type,
        scope_key=scope_key,
        project_scope=project_scope,
        repo_name=repo_name,
        active_only=active_only,
        limit=limit,
    )


@mcp.tool()
def issue_guardrails(
    error_text: str = "",
    context: str = "",
    command: str = "",
    file_path: str = "",
    repo_name: str = "",
    project_scope: str = "global",
    user_scope: str = "",
    limit: int = 5,
) -> dict:
    """Return proactive prevention rules and preferred strategies before an issue repeats."""
    return get_app().issue_guardrails(
        error_text=error_text,
        context=context,
        command=command,
        file_path=file_path,
        repo_name=repo_name,
        project_scope=project_scope,
        user_scope=user_scope,
        limit=limit,
    )


@mcp.tool()
def issue_metrics(window_days: int = 30) -> dict:
    """Return operational metrics for observability and production monitoring."""
    return get_app().issue_metrics(window_days=window_days)


@mcp.tool()
def issue_review_queue(status: str = "pending", limit: int = 20) -> dict:
    """List pending or resolved review items created by conservative consolidation."""
    return get_app().issue_review_queue(status=status, limit=limit)


@mcp.tool()
def issue_review_resolve(review_id: int, decision: str, note: str = "") -> dict:
    """Resolve a review queue item and activate or archive its provisional variant."""
    return get_app().issue_review_resolve(review_id=review_id, decision=decision, note=note)


@mcp.tool()
def issue_get(pattern_id: int, include_examples: bool = True, examples_limit: int = 5) -> dict:
    """Return the full stored details for one issue pattern, including variants and episodes."""
    return get_app().issue_get(pattern_id=pattern_id, include_examples=include_examples, examples_limit=examples_limit)


@mcp.tool()
def issue_recent(limit: int = 5, project_scope: str = "") -> dict:
    """Return recent issue patterns in compact form."""
    return get_app().issue_recent(limit=limit, project_scope=project_scope)


@mcp.tool()
def issue_search(query: str, project_scope: str = "", limit: int = 5, session_id: str = "") -> dict:
    """Keyword search across stored issue patterns."""
    return get_app().issue_search(query=query, project_scope=project_scope, limit=limit, session_id=session_id)


def main() -> None:
    lifecycle = get_lifecycle()
    try:
        lifecycle.start()
    except MCPServerOwnerConflict as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(exc.exit_code) from exc
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from exc
    try:
        mcp.run()
    except KeyboardInterrupt:
        if not lifecycle.shutdown_reason:
            raise
    finally:
        lifecycle.release()


if __name__ == "__main__":
    main()
