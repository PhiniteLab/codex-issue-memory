from __future__ import annotations

from dataclasses import dataclass
import statistics
import time
from typing import Any

from .dense_bandit import seed_dense_bandit_memory
from .user_domains import USER_DOMAIN_SEED_CASES, seed_user_domain_memory


@dataclass(frozen=True, slots=True)
class RealWorldEvalCase:
    slug: str
    mode: str
    error_text: str
    file_path: str
    command: str
    repo_name: str
    project_scope: str = "global"
    expected_title: str | None = None
    expected_status: str | None = None
    error_family: str | None = None


def _realistic_positive_cases() -> list[RealWorldEvalCase]:
    cases: list[RealWorldEvalCase] = []
    for payload in USER_DOMAIN_SEED_CASES[:12]:
        title = str(payload["title"])
        raw_error = str(payload["raw_error"])
        context = str(payload.get("context", ""))
        file_path = str(payload.get("file_path", ""))
        command = str(payload.get("command", ""))
        repo_name = str(payload.get("repo_name", ""))
        error_family = str(payload.get("error_family", ""))
        realistic_trace = "\n".join(
            [
                f"[run] repo={repo_name} cmd={command}",
                f"[file] {file_path}",
                f"[context] {context}",
                raw_error,
                "Traceback (most recent call last):",
                f'  File "{file_path}", line 41, in <module>',
                "    raise RuntimeError('propagated failure after noisy retry loop')",
            ]
        )
        cases.append(
            RealWorldEvalCase(
                slug=title.lower().replace(" ", "-"),
                mode="positive",
                error_text=realistic_trace,
                file_path=file_path,
                command=command,
                repo_name=repo_name,
                expected_title=title,
                error_family=error_family,
            )
        )
    return cases


NEGATIVE_REAL_WORLD_CASES: list[RealWorldEvalCase] = [
    RealWorldEvalCase(
        slug="npm-react-build",
        mode="negative",
        error_text="[webpack] Module parse failed: Unexpected token '<' while bundling React dashboard widget with JSX syntax.",
        file_path="frontend/dashboard/App.jsx",
        command="npm run build",
        repo_name="misc-ui",
        expected_status="abstain",
    ),
    RealWorldEvalCase(
        slug="ssh-public-key",
        mode="negative",
        error_text="git@github.com: Permission denied (publickey). fatal: Could not read from remote repository.",
        file_path=".ssh/config",
        command="git pull",
        repo_name="misc-git",
        expected_status="abstain",
    ),
    RealWorldEvalCase(
        slug="latex-bibliography",
        mode="negative",
        error_text="LaTeX Warning: Citation `hjb1967` undefined and bibliography style not found in thesis build.",
        file_path="paper/main.tex",
        command="latexmk -pdf paper/main.tex",
        repo_name="thesis-repo",
        expected_status="abstain",
    ),
    RealWorldEvalCase(
        slug="api-rate-limit",
        mode="negative",
        error_text="HTTP 429 rate_limit_exceeded while calling a remote embedding API from notebook worker.",
        file_path="notebooks/embed.py",
        command="python embed.py",
        repo_name="misc-remote-api",
        expected_status="abstain",
    ),
    RealWorldEvalCase(
        slug="docker-buildx",
        mode="negative",
        error_text="docker: 'buildx' is not a docker command in this remote lab image.",
        file_path="Dockerfile",
        command="docker buildx build .",
        repo_name="misc-docker",
        expected_status="abstain",
    ),
    RealWorldEvalCase(
        slug="segfault-renderer",
        mode="negative",
        error_text="Segmentation fault in custom C++ renderer while previewing aerodynamic mesh.",
        file_path="render/native.cpp",
        command="python preview_mesh.py",
        repo_name="misc-render",
        expected_status="abstain",
    ),
    RealWorldEvalCase(
        slug="excel-formula",
        mode="negative",
        error_text="Spreadsheet formula references a deleted sheet and now returns #REF! in report summary.",
        file_path="reports/summary.xlsx",
        command="python audit_reports.py",
        repo_name="misc-spreadsheets",
        expected_status="abstain",
    ),
    RealWorldEvalCase(
        slug="grammar-style-note",
        mode="negative",
        error_text="The abstract sounds too informal and the tone should be more academic.",
        file_path="paper/abstract.md",
        command="python style_check.py",
        repo_name="misc-writing",
        expected_status="abstain",
    ),
]


POSITIVE_REAL_WORLD_CASES = _realistic_positive_cases()


def seed_real_world_memory(app: Any) -> None:
    seed_user_domain_memory(app)
    seed_dense_bandit_memory(app)


def run_real_world_eval(app: Any, *, repeats: int = 1, limit: int = 3) -> dict[str, Any]:
    positives = POSITIVE_REAL_WORLD_CASES
    negatives = NEGATIVE_REAL_WORLD_CASES
    latencies: list[float] = []
    decision_hist: dict[str, int] = {}
    family_totals: dict[str, dict[str, int]] = {}
    failures: list[dict[str, Any]] = []
    positive_total = 0
    positive_top1 = 0
    clear_matches = 0
    clear_correct = 0
    negative_total = 0
    negative_safe = 0

    for _ in range(max(repeats, 1)):
        for case in positives + negatives:
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
            latencies.append(latency_ms)
            status = str(result["decision"]["status"])
            decision_hist[status] = decision_hist.get(status, 0) + 1
            top_title = str(result["matches"][0]["title"]) if result.get("matches") else ""
            if case.mode == "positive":
                positive_total += 1
                family = case.error_family or "unknown"
                bucket = family_totals.setdefault(family, {"total": 0, "top1": 0})
                bucket["total"] += 1
                if top_title == case.expected_title and status in {"match", "ambiguous"}:
                    positive_top1 += 1
                    bucket["top1"] += 1
                else:
                    failures.append({
                        "slug": case.slug,
                        "mode": case.mode,
                        "status": status,
                        "expected_title": case.expected_title,
                        "top_title": top_title,
                    })
                if status == "match":
                    clear_matches += 1
                    if top_title == case.expected_title:
                        clear_correct += 1
            else:
                negative_total += 1
                if status in {"abstain", "ambiguous"}:
                    negative_safe += 1
                else:
                    failures.append({
                        "slug": case.slug,
                        "mode": case.mode,
                        "status": status,
                        "expected_status": case.expected_status,
                        "top_title": top_title,
                    })

    family_accuracy = {
        family: round(bucket["top1"] / max(bucket["total"], 1), 4)
        for family, bucket in sorted(family_totals.items())
    }
    return {
        "dataset": "real_world_eval",
        "positives": len(positives),
        "negatives": len(negatives),
        "top1_accuracy": round(positive_top1 / max(positive_total, 1), 4),
        "clear_match_precision": round(clear_correct / max(clear_matches, 1), 4),
        "negative_safety_rate": round(negative_safe / max(negative_total, 1), 4),
        "decision_histogram": decision_hist,
        "family_top1_accuracy": family_accuracy,
        "latency_ms": {
            "mean": round(statistics.mean(latencies), 3) if latencies else 0.0,
            "median": round(statistics.median(latencies), 3) if latencies else 0.0,
            "p95": round(sorted(latencies)[max(int(len(latencies) * 0.95) - 1, 0)], 3) if latencies else 0.0,
            "max": round(max(latencies), 3) if latencies else 0.0,
        },
        "failures": failures[:20],
    }


__all__ = [
    "NEGATIVE_REAL_WORLD_CASES",
    "POSITIVE_REAL_WORLD_CASES",
    "RealWorldEvalCase",
    "run_real_world_eval",
    "seed_real_world_memory",
]
