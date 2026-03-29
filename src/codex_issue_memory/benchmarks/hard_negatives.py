from __future__ import annotations

from dataclasses import dataclass
import statistics
import time
from typing import Any

from .dense_bandit import seed_dense_bandit_memory
from .user_domains import seed_user_domain_memory


@dataclass(frozen=True, slots=True)
class HardNegativeCase:
    slug: str
    mode: str
    error_text: str
    file_path: str
    command: str
    repo_name: str
    expected_title: str | None = None
    expected_status: str | None = None
    project_scope: str = "global"


POSITIVE_HARD_NEGATIVE_CASES: list[HardNegativeCase] = [
    HardNegativeCase(
        slug="requests-api-worker",
        mode="positive",
        error_text="ModuleNotFoundError: No module named requests while starting the API worker under gunicorn bootstrap",
        file_path="api/worker.py",
        command="python worker.py",
        repo_name="tooling-lab",
        expected_title="Requests missing in API worker",
    ),
    HardNegativeCase(
        slug="requests-cli-interpreter",
        mode="positive",
        error_text="ImportError: cannot import name requests during CLI bootstrap because the wrong interpreter is active",
        file_path="cli/bootstrap.py",
        command="python cli.py",
        repo_name="tooling-lab",
        expected_title="Requests missing because CLI uses wrong interpreter",
    ),
    HardNegativeCase(
        slug="hjb-grid-path",
        mode="positive",
        error_text="FileNotFoundError: data/hjb/value_grid.mat when starting the hjb solver outside the repository root",
        file_path="control/hjb/load_value_grid.py",
        command="python solve_hjb.py --grid data/hjb/value_grid.mat",
        repo_name="optimal-control-suite",
        expected_title="HJB value grid file path failure",
    ),
    HardNegativeCase(
        slug="bellman-checkpoint-path",
        mode="positive",
        error_text="FileNotFoundError: checkpoints/bellman_backup_latest.pt after resuming dynamic programming experiment from another cwd",
        file_path="control/dp/resume.py",
        command="python run_bellman_backup.py --resume latest",
        repo_name="optimal-control-suite",
        expected_title="Bellman backup checkpoint path mismatch",
    ),
    HardNegativeCase(
        slug="gguf-model-path",
        mode="positive",
        error_text="FileNotFoundError: models/mistral-7b-instruct.gguf while starting local llm inference server from another directory",
        file_path="llm/local_server/model_loader.py",
        command="python serve_local_llm.py --model mistral-7b-instruct.gguf",
        repo_name="local-llm-stack",
        expected_title="Local LLM GGUF model path failure",
    ),
    HardNegativeCase(
        slug="matlab-engine-import",
        mode="positive",
        error_text="ModuleNotFoundError: No module named matlab.engine while importing the HJB preprocessing bridge",
        file_path="matlab_bridge/startup.py",
        command="python run_matlab_bridge.py --task hjb_precompute",
        repo_name="optimal-control-suite",
        expected_title="MATLAB engine module missing",
    ),
]

NEGATIVE_HARD_NEGATIVE_CASES: list[HardNegativeCase] = [
    HardNegativeCase(
        slug="requests-underspecified",
        mode="negative",
        error_text="ModuleNotFoundError: No module named requests",
        file_path="",
        command="python entrypoint.py",
        repo_name="tooling-lab",
        expected_status="ambiguous",
    ),
    HardNegativeCase(
        slug="generic-cwd-path",
        mode="negative",
        error_text="FileNotFoundError while launching from another working directory because the asset path depends on cwd",
        file_path="unknown/path_loader.py",
        command="python run_job.py",
        repo_name="misc-repo",
        expected_status="ambiguous",
    ),
    HardNegativeCase(
        slug="generic-device-mix",
        mode="negative",
        error_text="RuntimeError: Expected all tensors to be on the same device, but found cpu and cuda during training",
        file_path="trainer/train.py",
        command="python train.py --device cuda",
        repo_name="vision-trainer",
        expected_status="ambiguous",
    ),
    HardNegativeCase(
        slug="generic-missing-config-key",
        mode="negative",
        error_text="KeyError: gamma_iss while parsing controller config and validation is missing",
        file_path="control/config_loader.py",
        command="python run_controller.py",
        repo_name="optimal-control-suite",
        expected_status="ambiguous",
    ),
    HardNegativeCase(
        slug="out-of-scope-java-build",
        mode="negative",
        error_text="javac: invalid target release: 21 while compiling Android support library",
        file_path="android/build.gradle",
        command="./gradlew build",
        repo_name="android-lab",
        expected_status="abstain",
    ),
]


def seed_hard_negative_memory(app: Any) -> None:
    seed_user_domain_memory(app)
    seed_dense_bandit_memory(app)


def run_hard_negative_benchmark(app: Any, *, repeats: int = 1, limit: int = 3) -> dict[str, Any]:
    latencies: list[float] = []
    failures: list[dict[str, Any]] = []
    positive_total = 0
    positive_top1 = 0
    negative_total = 0
    negative_safe = 0
    unsafe_clear_matches = 0

    for _ in range(max(repeats, 1)):
        for case in POSITIVE_HARD_NEGATIVE_CASES + NEGATIVE_HARD_NEGATIVE_CASES:
            start = time.perf_counter()
            result = app.issue_match(
                error_text=case.error_text,
                file_path=case.file_path,
                command=case.command,
                repo_name=case.repo_name,
                project_scope=case.project_scope,
                limit=limit,
            )
            latencies.append((time.perf_counter() - start) * 1000.0)
            status = str(result["decision"]["status"])
            top_title = str(result["matches"][0]["title"]) if result.get("matches") else ""
            if case.mode == "positive":
                positive_total += 1
                if top_title == case.expected_title and status in {"match", "ambiguous"}:
                    positive_top1 += 1
                else:
                    failures.append({
                        "slug": case.slug,
                        "mode": case.mode,
                        "expected_title": case.expected_title,
                        "status": status,
                        "top_title": top_title,
                    })
            else:
                negative_total += 1
                safe_statuses = {str(case.expected_status), "abstain" if case.expected_status == "ambiguous" else str(case.expected_status)}
                if status in safe_statuses:
                    negative_safe += 1
                else:
                    unsafe_clear_matches += 1 if status == "match" else 0
                    failures.append({
                        "slug": case.slug,
                        "mode": case.mode,
                        "expected_status": case.expected_status,
                        "status": status,
                        "top_title": top_title,
                    })

    return {
        "dataset": "hard_negatives",
        "positive_top1_accuracy": round(positive_top1 / max(positive_total, 1), 4),
        "negative_safety_rate": round(negative_safe / max(negative_total, 1), 4),
        "unsafe_clear_match_rate": round(unsafe_clear_matches / max(negative_total, 1), 4),
        "latency_ms": {
            "mean": round(statistics.mean(latencies), 3) if latencies else 0.0,
            "median": round(statistics.median(latencies), 3) if latencies else 0.0,
            "p95": round(sorted(latencies)[max(int(len(latencies) * 0.95) - 1, 0)], 3) if latencies else 0.0,
        },
        "failures": failures[:20],
    }


__all__ = [
    "HardNegativeCase",
    "NEGATIVE_HARD_NEGATIVE_CASES",
    "POSITIVE_HARD_NEGATIVE_CASES",
    "run_hard_negative_benchmark",
    "seed_hard_negative_memory",
]
