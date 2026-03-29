from __future__ import annotations

import statistics
import time
from typing import Any


DENSE_PARAPHRASE_CASES: list[dict[str, str]] = [
    {
        "title": "LoRA adapter checkpoint rank mismatch",
        "raw_error": "ValueError: LoRA adapter rank 16 does not match active base model adapter rank 8 during model warm start",
        "canonical_fix": "Load the adapter with the same base model id and LoRA rank used to create the checkpoint.",
        "prevention_rule": "Persist base-model id and LoRA rank in checkpoint metadata and validate them before loading.",
        "canonical_symptom": "llm warm start fails because the saved lora adapter dimensions do not match the active base model",
        "verification_steps": "Load the checkpoint and verify stored base-model id and adapter rank equal the runtime model configuration.",
        "tags": "llm,lora,checkpoint,adapter,rank",
        "error_family": "model_state_error",
        "root_cause_class": "checkpoint_model_shape_mismatch",
        "context": "Local LLM fine-tuning pipeline with LoRA adapters.",
        "file_path": "llm/lora/load_adapter.py",
        "command": "python train_lora.py --resume adapter.ckpt",
        "domain": "llm-development",
        "repo_name": "llm-lab",
        "query": "active runtime uses an incompatible base checkpoint for the low-rank adapter during local llm warm start",
    },
    {
        "title": "HJB value grid file path failure",
        "raw_error": "FileNotFoundError: ./data/hjb/value_grid.mat while launching HJB solver from outside repo root",
        "canonical_fix": "Resolve the HJB value grid path relative to the solver module instead of the runtime cwd.",
        "prevention_rule": "Never load HJB assets via cwd-relative paths.",
        "canonical_symptom": "hjb solver cannot load precomputed value grid when started outside the repository root",
        "verification_steps": "Run the HJB solver from repo root and external cwd using the same asset path.",
        "tags": "control,hjb,value-grid,matlab,path",
        "error_family": "path_resolution_error",
        "root_cause_class": "cwd_relative_path_bug",
        "context": "Hamilton-Jacobi-Bellman offline grid solver importing MATLAB value function tables.",
        "file_path": "control/hjb/load_value_grid.py",
        "command": "python solve_hjb.py --grid data/hjb/value_grid.mat",
        "domain": "control-theory",
        "repo_name": "optimal-control-suite",
        "query": "precomputed value-function table loads only when launched from the repository root; external working directory loses the HJB grid asset",
    },
    {
        "title": "Telemetry JSON log malformed",
        "raw_error": "json.decoder.JSONDecodeError: Expecting ',' delimiter in experiment telemetry payload",
        "canonical_fix": "Validate and re-serialize telemetry payloads before appending them to the experiment log.",
        "prevention_rule": "Never append raw partially formatted telemetry strings to JSONL logs.",
        "canonical_symptom": "experiment telemetry pipeline fails because malformed json is appended to the structured log",
        "verification_steps": "Run telemetry serialization validation before writing the log row.",
        "tags": "json,telemetry,logging,experiments",
        "error_family": "serialization_error",
        "root_cause_class": "malformed_json_payload",
        "context": "Research experiment tracking pipeline writing JSON telemetry to disk.",
        "file_path": "ops/telemetry/write_jsonl.py",
        "command": "python run_experiment.py --emit-telemetry",
        "domain": "research-ops",
        "repo_name": "exp-ops",
        "query": "structured experiment logging crashes because the emitted telemetry row is not valid json before it is appended to the tracking file",
    },
]


def seed_dense_bandit_memory(app: Any) -> None:
    for payload in DENSE_PARAPHRASE_CASES:
        app.issue_record_resolution(**{key: value for key, value in payload.items() if key != "query"})
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
        repo_name="tooling-lab",
    )
    app.issue_record_resolution(
        title="Requests missing because CLI uses wrong interpreter",
        raw_error="ImportError: cannot import name requests from CLI utility bootstrap",
        canonical_fix="Run the CLI with the project virtualenv interpreter that already has requests installed.",
        prevention_rule="Pin the CLI launcher to the intended project virtualenv and validate the active interpreter before startup.",
        project_scope="global",
        canonical_symptom="requests import fails during cli bootstrap because the wrong interpreter is active",
        verification_steps="Run the CLI import check with the intended virtualenv interpreter.",
        tags="python,import,requests,cli,venv",
        error_family="import_error",
        root_cause_class="missing_python_module",
        command="python cli.py",
        file_path="cli/bootstrap.py",
        domain="python",
        repo_name="tooling-lab",
    )


def run_dense_bandit_benchmark(app: Any, *, repeats: int = 5) -> dict[str, Any]:
    dense_hits = 0
    dense_total = 0
    top1_correct = 0
    latencies: list[float] = []

    for _ in range(max(repeats, 1)):
        for payload in DENSE_PARAPHRASE_CASES:
            start = time.perf_counter()
            result = app.issue_match(
                error_text=payload["query"],
                file_path=payload["file_path"],
                command=payload["command"],
                repo_name=payload["repo_name"],
                project_scope="global",
                limit=3,
            )
            latencies.append((time.perf_counter() - start) * 1000.0)
            dense_total += 1
            if result["matches"] and result["matches"][0]["title"] == payload["title"]:
                top1_correct += 1
            if result["matches"] and "dense-retrieval" in result["matches"][0]["why"]:
                dense_hits += 1

    first = app.issue_match(
        error_text="ModuleNotFoundError: No module named requests",
        project_scope="global",
        session_id="bandit-seed",
        repo_name="tooling-lab",
        limit=3,
    )
    second_pattern = int(first["matches"][1]["pattern_id"])
    feedback = app.issue_feedback(
        retrieval_event_id=int(first["retrieval_event_id"]),
        feedback_type="fix_verified",
        candidate_rank=2,
        notes="Benchmark positive feedback for second candidate.",
    )
    second = app.issue_match(
        error_text="ModuleNotFoundError: No module named requests",
        project_scope="global",
        session_id="bandit-fresh",
        repo_name="tooling-lab",
        limit=3,
    )
    bandit_promoted = bool(second["matches"]) and int(second["matches"][0]["pattern_id"]) == second_pattern
    bandit_reason = second["matches"][0]["why"] if second["matches"] else []

    return {
        "dense_top1_accuracy": round(top1_correct / max(dense_total, 1), 4),
        "dense_reason_rate": round(dense_hits / max(dense_total, 1), 4),
        "latency_ms": {
            "mean": round(statistics.mean(latencies), 3) if latencies else 0.0,
            "median": round(statistics.median(latencies), 3) if latencies else 0.0,
            "p95": round(sorted(latencies)[max(int(len(latencies) * 0.95) - 1, 0)], 3) if latencies else 0.0,
            "max": round(max(latencies), 3) if latencies else 0.0,
        },
        "bandit": {
            "promoted_second_candidate": bandit_promoted,
            "feedback": feedback["bandit"],
            "top_match": second["matches"][0] if second["matches"] else None,
            "why": bandit_reason,
        },
    }
