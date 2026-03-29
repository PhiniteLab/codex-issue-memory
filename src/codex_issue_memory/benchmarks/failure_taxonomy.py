from __future__ import annotations

from dataclasses import dataclass
import re
import statistics
import time
from typing import Any

from .user_domains import USER_DOMAIN_SEED_CASES, seed_user_domain_memory


@dataclass(frozen=True, slots=True)
class FailureTaxonomyQueryCase:
    slug: str
    category: str
    mode: str
    error_text: str
    file_path: str
    command: str
    repo_name: str
    project_scope: str = "global"
    expected_title: str | None = None
    expected_status: str | None = None


NEGATIVE_ABSTAIN_CASES: list[FailureTaxonomyQueryCase] = [
    FailureTaxonomyQueryCase(
        slug="segfault-c-extension",
        category="out_of_distribution_runtime",
        mode="negative",
        error_text="Segmentation fault in custom C extension while rendering OpenGL preview",
        file_path="graphics/render.cpp",
        command="python render.py",
        repo_name="misc-repo",
        expected_status="abstain",
    ),
    FailureTaxonomyQueryCase(
        slug="dns-timeout",
        category="network_out_of_domain",
        mode="negative",
        error_text="Connection timed out reaching http://telemetry.remote/api from lab cluster",
        file_path="infra/network.py",
        command="python ping_service.py",
        repo_name="misc-repo",
        expected_status="abstain",
    ),
    FailureTaxonomyQueryCase(
        slug="grammar-style",
        category="natural_language_non_error",
        mode="negative",
        error_text="The introduction paragraph feels too long and needs better academic tone",
        file_path="paper/intro.tex",
        command="python style_check.py",
        repo_name="misc-repo",
        expected_status="abstain",
    ),
    FailureTaxonomyQueryCase(
        slug="bellman-proof",
        category="theoretical_question_non_error",
        mode="negative",
        error_text="Need proof that Bellman operator is a contraction under discounted sup norm",
        file_path="notes/bellman.md",
        command="python run_notes.py",
        repo_name="misc-repo",
        expected_status="abstain",
    ),
    FailureTaxonomyQueryCase(
        slug="ssh-publickey",
        category="ssh_auth_out_of_memory_scope",
        mode="negative",
        error_text="Permission denied (publickey) while ssh into git remote",
        file_path=".ssh/config",
        command="git pull",
        repo_name="misc-repo",
        expected_status="abstain",
    ),
    FailureTaxonomyQueryCase(
        slug="frontend-syntax",
        category="frontend_javascript",
        mode="negative",
        error_text="SyntaxError: Unexpected token => in bundled frontend dashboard code",
        file_path="frontend/app.js",
        command="npm run build",
        repo_name="misc-repo",
        expected_status="abstain",
    ),
    FailureTaxonomyQueryCase(
        slug="cuda-driver",
        category="cuda_runtime_compatibility",
        mode="negative",
        error_text="CUDA driver version is insufficient for CUDA runtime version",
        file_path="train.py",
        command="python train.py --cuda",
        repo_name="misc-repo",
        expected_status="abstain",
    ),
    FailureTaxonomyQueryCase(
        slug="latex-hbox",
        category="latex_typesetting",
        mode="negative",
        error_text="Overfull hbox in paragraph at lines 20--22",
        file_path="paper/main.tex",
        command="latexmk",
        repo_name="misc-repo",
        expected_status="abstain",
    ),
    FailureTaxonomyQueryCase(
        slug="docker-compose",
        category="container_tooling",
        mode="negative",
        error_text="docker: 'compose' is not a docker command",
        file_path="Dockerfile",
        command="docker compose up",
        repo_name="misc-repo",
        expected_status="abstain",
    ),
    FailureTaxonomyQueryCase(
        slug="random-note",
        category="plain_note",
        mode="negative",
        error_text="Remember to buy coffee and update the syllabus office hours",
        file_path="notes/todo.txt",
        command="cat notes/todo.txt",
        repo_name="misc-repo",
        expected_status="abstain",
    ),
]


def _squash(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def build_positive_taxonomy_cases() -> list[FailureTaxonomyQueryCase]:
    cases: list[FailureTaxonomyQueryCase] = []
    for payload in USER_DOMAIN_SEED_CASES:
        expected_title = str(payload["title"])
        repo_name = str(payload["repo_name"])
        domain = str(payload.get("domain", "generic"))
        cases.extend(
            [
                FailureTaxonomyQueryCase(
                    slug=f"{expected_title.lower().replace(' ', '-')}:raw",
                    category=domain,
                    mode="raw_error",
                    error_text=str(payload["raw_error"]),
                    file_path="",
                    command="",
                    repo_name=repo_name,
                    expected_title=expected_title,
                ),
                FailureTaxonomyQueryCase(
                    slug=f"{expected_title.lower().replace(' ', '-')}:symptom_noise",
                    category=domain,
                    mode="symptom_noise",
                    error_text=f"{payload['canonical_symptom']} after refactor. also touched src/utils/io.py",
                    file_path="src/utils/io.py",
                    command="python run.py --debug",
                    repo_name=repo_name,
                    expected_title=expected_title,
                ),
                FailureTaxonomyQueryCase(
                    slug=f"{expected_title.lower().replace(' ', '-')}:context_exception",
                    category=domain,
                    mode="context_exception",
                    error_text=_squash(f"{payload['context']} {str(payload['raw_error']).split(':', 1)[0]}"),
                    file_path=str(payload.get("file_path", "")),
                    command=str(payload.get("command", "")),
                    repo_name=repo_name,
                    expected_title=expected_title,
                ),
                FailureTaxonomyQueryCase(
                    slug=f"{expected_title.lower().replace(' ', '-')}:telemetry_noise",
                    category=domain,
                    mode="telemetry_noise",
                    error_text=_squash(f"{payload['raw_error']} while updating telemetry json and experiment repo tooling"),
                    file_path=str(payload.get("file_path", "")),
                    command=(str(payload.get("command", "")) + " --log-json").strip(),
                    repo_name=repo_name,
                    expected_title=expected_title,
                ),
            ]
        )
    return cases


POSITIVE_TAXONOMY_CASES = build_positive_taxonomy_cases()


def run_failure_taxonomy_benchmark(app: Any, *, repeats: int = 1, limit: int = 3) -> dict[str, Any]:
    positive_cases = POSITIVE_TAXONOMY_CASES
    negative_cases = NEGATIVE_ABSTAIN_CASES
    all_latencies: list[float] = []
    positive_latencies: list[float] = []
    negative_latencies: list[float] = []
    decision_histogram: dict[str, int] = {}
    positive_failures: list[dict[str, Any]] = []
    negative_failures: list[dict[str, Any]] = []
    positive_total = 0
    positive_top1 = 0
    positive_actionable = 0
    negative_total = 0
    negative_abstain = 0

    for _repeat in range(max(repeats, 1)):
        for case in positive_cases:
            start = time.perf_counter()
            result = app.issue_match(
                error_text=case.error_text,
                file_path=case.file_path,
                command=case.command,
                repo_name=case.repo_name,
                project_scope=case.project_scope,
                limit=limit,
            )
            latency_ms = (time.perf_counter() - start) * 1000.0
            all_latencies.append(latency_ms)
            positive_latencies.append(latency_ms)
            positive_total += 1
            status = str(result["decision"]["status"])
            decision_histogram[status] = decision_histogram.get(status, 0) + 1
            top_title = result["matches"][0]["title"] if result["matches"] else None
            if top_title == case.expected_title:
                positive_top1 += 1
            if status in {"match", "ambiguous"} and top_title == case.expected_title:
                positive_actionable += 1
            else:
                positive_failures.append(
                    {
                        "slug": case.slug,
                        "category": case.category,
                        "mode": case.mode,
                        "expected_title": case.expected_title,
                        "top_title": top_title,
                        "decision": result["decision"],
                        "matches": result["matches"][:3],
                    }
                )

        for case in negative_cases:
            start = time.perf_counter()
            result = app.issue_match(
                error_text=case.error_text,
                file_path=case.file_path,
                command=case.command,
                repo_name=case.repo_name,
                project_scope=case.project_scope,
                limit=limit,
            )
            latency_ms = (time.perf_counter() - start) * 1000.0
            all_latencies.append(latency_ms)
            negative_latencies.append(latency_ms)
            negative_total += 1
            status = str(result["decision"]["status"])
            decision_histogram[status] = decision_histogram.get(status, 0) + 1
            if status == "abstain":
                negative_abstain += 1
            else:
                negative_failures.append(
                    {
                        "slug": case.slug,
                        "category": case.category,
                        "expected_status": case.expected_status,
                        "decision": result["decision"],
                        "matches": result["matches"][:3],
                    }
                )

    def _latency_stats(values: list[float]) -> dict[str, float]:
        if not values:
            return {"mean": 0.0, "median": 0.0, "p95": 0.0, "max": 0.0}
        ordered = sorted(values)
        p95_index = max(min(int(round(0.95 * (len(ordered) - 1))), len(ordered) - 1), 0)
        return {
            "mean": round(statistics.mean(values), 3),
            "median": round(statistics.median(values), 3),
            "p95": round(ordered[p95_index], 3),
            "max": round(ordered[-1], 3),
        }

    return {
        "seed_count": len(USER_DOMAIN_SEED_CASES),
        "positive_case_count": len(positive_cases),
        "negative_case_count": len(negative_cases),
        "repeats": max(repeats, 1),
        "positive_total_runs": positive_total,
        "negative_total_runs": negative_total,
        "positive_top1_accuracy": round(positive_top1 / max(positive_total, 1), 4),
        "positive_actionable_rate": round(positive_actionable / max(positive_total, 1), 4),
        "negative_abstain_rate": round(negative_abstain / max(negative_total, 1), 4),
        "decision_histogram": decision_histogram,
        "latency_ms": {
            "overall": _latency_stats(all_latencies),
            "positive": _latency_stats(positive_latencies),
            "negative": _latency_stats(negative_latencies),
        },
        "positive_failures": positive_failures[:12],
        "negative_failures": negative_failures[:12],
    }


def run_runtime_diagnostics(app: Any, *, repeats: int = 8, limit: int = 3) -> dict[str, Any]:
    seed_user_domain_memory(app)
    taxonomy = run_failure_taxonomy_benchmark(app, repeats=max(repeats, 1), limit=limit)

    consolidation_latencies: list[float] = []
    consolidation_ids: list[tuple[int, int]] = []
    for index in range(40):
        start = time.perf_counter()
        result = app.issue_record_resolution(
            title="PPO advantage minibatch shape mismatch",
            raw_error="RuntimeError: mat1 and mat2 shapes cannot be multiplied in PPO advantage head during minibatch update",
            canonical_fix="Ensure GAE advantages are flattened to [batch,1] before passing to the PPO value head.",
            prevention_rule="Keep PPO rollout tensors and minibatch tensors shape contracts explicit.",
            project_scope="global",
            canonical_symptom="ppo minibatch update fails because value head receives rank mismatched advantage tensor",
            verification_steps="Run PPO update with the same rollout buffer and assert advantage.shape == [batch,1].",
            tags="rl,ppo,gae,pytorch,shape",
            error_family="tensor_shape_error",
            root_cause_class="tensor_rank_or_dim_mismatch",
            context="RL training loop for PPO on continuous control benchmark.",
            file_path="rl/ppo/advantage.py",
            command="python train_ppo.py --algo ppo --env HalfCheetah-v4",
            domain="rl-control",
            repo_name="rl-control-lab",
            git_commit=f"diag-{index:03d}",
            session_id="runtime-diagnostics",
            verification_output="ok",
            resolution_notes="runtime diagnostic repetition",
            patch_summary="flatten advantage tensor before value head forward",
        )
        consolidation_latencies.append((time.perf_counter() - start) * 1000.0)
        consolidation_ids.append((int(result["pattern_id"]), int(result["variant_id"])))

    session_id = "runtime-feedback-session"
    app.issue_record_resolution(
        title="Requests missing in API worker",
        raw_error="ModuleNotFoundError: No module named requests while starting API worker",
        canonical_fix="Install requests into the active environment used by the API worker.",
        prevention_rule="Pin and install runtime dependencies in the worker environment.",
        project_scope="global",
        canonical_symptom="requests import fails during api worker startup",
        verification_steps="Run the API worker import check inside the same environment.",
        tags="python,import,requests,api",
        error_family="import_error",
        root_cause_class="missing_python_module",
        command="python worker.py",
        file_path="api/worker.py",
        domain="python",
    )
    app.issue_record_resolution(
        title="Requests missing in CLI utility",
        raw_error="ImportError: cannot import name requests from CLI utility bootstrap",
        canonical_fix="Install requests into the environment used by the CLI entrypoint.",
        prevention_rule="Keep CLI dependencies synchronized with runtime requirements.",
        project_scope="global",
        canonical_symptom="requests import fails during cli bootstrap",
        verification_steps="Run the CLI import check inside the same environment.",
        tags="python,import,requests,cli",
        error_family="import_error",
        root_cause_class="missing_python_module",
        command="python cli.py",
        file_path="cli/bootstrap.py",
        domain="python",
    )
    first = app.issue_match(
        error_text="ModuleNotFoundError: No module named requests",
        project_scope="global",
        session_id=session_id,
        limit=3,
    )
    rerank_ok = False
    if first["matches"] and first["retrieval_event_id"] is not None:
        top_pattern_before = int(first["matches"][0]["pattern_id"])
        app.issue_feedback(
            retrieval_event_id=int(first["retrieval_event_id"]),
            feedback_type="candidate_rejected",
            candidate_rank=1,
            notes="runtime diagnostics rejection",
        )
        second = app.issue_match(
            error_text="ModuleNotFoundError: No module named requests",
            project_scope="global",
            session_id=session_id,
            limit=3,
        )
        rerank_ok = bool(second["matches"]) and int(second["matches"][0]["pattern_id"]) != top_pattern_before
    else:
        second = {"matches": []}

    with app.store.managed_connection() as conn:
        integrity = str(conn.execute("PRAGMA integrity_check").fetchone()[0])

    distinct_pattern_ids = {pattern_id for pattern_id, _variant_id in consolidation_ids}
    distinct_variant_ids = {variant_id for _pattern_id, variant_id in consolidation_ids}
    return {
        "taxonomy": taxonomy,
        "consolidation": {
            "runs": len(consolidation_latencies),
            "latency_ms": {
                "mean": round(statistics.mean(consolidation_latencies), 3),
                "median": round(statistics.median(consolidation_latencies), 3),
                "p95": round(sorted(consolidation_latencies)[max(int(round(0.95 * (len(consolidation_latencies) - 1))), 0)], 3),
                "max": round(max(consolidation_latencies), 3),
            },
            "distinct_pattern_ids": len(distinct_pattern_ids),
            "distinct_variant_ids": len(distinct_variant_ids),
        },
        "session_feedback_rerank_ok": rerank_ok,
        "integrity_check": integrity,
    }
