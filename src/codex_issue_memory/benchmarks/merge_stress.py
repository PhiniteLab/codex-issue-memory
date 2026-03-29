from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class MergeStressCase:
    slug: str
    first: dict[str, str]
    second: dict[str, str]
    expect_same_pattern: bool
    expect_same_variant: bool


MERGE_STRESS_CASES: list[MergeStressCase] = [
    MergeStressCase(
        slug="sqlite-exact-repeat",
        first={
            "title": "Relative sqlite path breaks outside repo root",
            "raw_error": "FileNotFoundError: references/contractsDatabase.sqlite3",
            "canonical_fix": "Resolve the SQLite path relative to __file__.",
            "prevention_rule": "No production DB path may depend on cwd.",
            "project_scope": "global",
            "canonical_symptom": "sqlite database path fails outside repo root",
            "verification_steps": "Run from repo root and external cwd.",
            "tags": "sqlite,path,cwd",
            "error_family": "sqlite_error",
            "root_cause_class": "cwd_relative_path_bug",
            "command": "python -m app.main",
            "file_path": "services/db_loader.py",
            "stack_excerpt": 'File "services/db_loader.py", line 12, in load_db',
            "repo_name": "research-ops-hub",
            "domain": "python",
        },
        second={
            "title": "Relative sqlite path breaks outside repo root",
            "raw_error": "FileNotFoundError: references/contractsDatabase.sqlite3",
            "canonical_fix": "Resolve the SQLite path relative to __file__ and normalize the final Path.",
            "prevention_rule": "No production DB path may depend on cwd.",
            "project_scope": "global",
            "canonical_symptom": "sqlite database path fails outside repo root",
            "verification_steps": "Run from repo root and external cwd.",
            "tags": "sqlite,path,cwd",
            "error_family": "sqlite_error",
            "root_cause_class": "cwd_relative_path_bug",
            "command": "python -m app.main",
            "file_path": "services/db_loader.py",
            "stack_excerpt": 'File "services/db_loader.py", line 12, in load_db',
            "repo_name": "research-ops-hub",
            "domain": "python",
        },
        expect_same_pattern=True,
        expect_same_variant=True,
    ),
    MergeStressCase(
        slug="import-different-module",
        first={
            "title": "Requests missing in API worker",
            "raw_error": "ModuleNotFoundError: No module named requests while starting API worker",
            "canonical_fix": "Install requests into the worker environment.",
            "prevention_rule": "Pin worker dependencies.",
            "project_scope": "global",
            "canonical_symptom": "requests import fails during api worker startup",
            "verification_steps": "Run the worker import check.",
            "tags": "python,import,requests,api",
            "error_family": "import_error",
            "root_cause_class": "missing_python_module",
            "command": "python worker.py",
            "file_path": "api/worker.py",
            "repo_name": "tooling-lab",
            "domain": "python",
        },
        second={
            "title": "MATLAB engine module missing",
            "raw_error": "ModuleNotFoundError: No module named matlab.engine while importing the HJB preprocessing bridge",
            "canonical_fix": "Install the MATLAB Python engine in the active environment.",
            "prevention_rule": "Validate matlab.engine before startup.",
            "project_scope": "global",
            "canonical_symptom": "matlab engine import fails in the preprocessing bridge",
            "verification_steps": "Run a startup import check for matlab.engine.",
            "tags": "python,import,matlab,engine",
            "error_family": "import_error",
            "root_cause_class": "missing_python_module",
            "command": "python run_matlab_bridge.py --task hjb_precompute",
            "file_path": "matlab_bridge/startup.py",
            "repo_name": "optimal-control-suite",
            "domain": "python",
        },
        expect_same_pattern=False,
        expect_same_variant=False,
    ),
    MergeStressCase(
        slug="config-different-key",
        first={
            "title": "ISS controller gain key missing",
            "raw_error": "KeyError: gamma_iss while parsing controller gains",
            "canonical_fix": "Add gamma_iss to the controller configuration and validate required keys at startup.",
            "prevention_rule": "Validate controller config keys before startup.",
            "project_scope": "global",
            "canonical_symptom": "iss controller startup fails because gamma_iss is absent",
            "verification_steps": "Assert gamma_iss exists before controller init.",
            "tags": "control,config,iss,yaml",
            "error_family": "config_error",
            "root_cause_class": "missing_required_config_key",
            "command": "python run_controller.py --mode iss",
            "file_path": "control/config_loader.py",
            "repo_name": "optimal-control-suite",
            "domain": "control-theory",
        },
        second={
            "title": "MPC controller gain key missing",
            "raw_error": "KeyError: alpha_terminal while parsing nonlinear MPC gains",
            "canonical_fix": "Add alpha_terminal to the MPC config and validate required keys at startup.",
            "prevention_rule": "Validate controller config keys before startup.",
            "project_scope": "global",
            "canonical_symptom": "mpc controller startup fails because alpha_terminal is absent",
            "verification_steps": "Assert alpha_terminal exists before controller init.",
            "tags": "control,config,mpc,yaml",
            "error_family": "config_error",
            "root_cause_class": "missing_required_config_key",
            "command": "python run_controller.py --mode mpc",
            "file_path": "control/config_loader.py",
            "repo_name": "optimal-control-suite",
            "domain": "control-theory",
        },
        expect_same_pattern=True,
        expect_same_variant=False,
    ),
    MergeStressCase(
        slug="tensor-device-different-cause",
        first={
            "title": "Optimizer state left on CPU after resume",
            "raw_error": "RuntimeError: Expected all tensors to be on the same device, but got cpu and cuda after checkpoint resume",
            "canonical_fix": "Move optimizer state tensors onto the active CUDA device after checkpoint resume.",
            "prevention_rule": "Every training state restore must re-home tensors to the active device.",
            "project_scope": "global",
            "canonical_symptom": "mixed cuda and cpu tensors after checkpoint resume",
            "verification_steps": "Resume from checkpoint and run one optimizer step.",
            "tags": "pytorch,cuda,cpu,training",
            "error_family": "tensor_device_error",
            "root_cause_class": "tensor_cross_device_mix",
            "command": "python train.py --resume outputs/ckpt.pt",
            "file_path": "trainer/checkpoint_loader.py",
            "stack_excerpt": 'File "trainer/checkpoint_loader.py", line 42, in restore_optimizer_state',
            "repo_name": "vision-trainer",
            "domain": "pytorch",
        },
        second={
            "title": "Dataloader batch left on CPU",
            "raw_error": "RuntimeError: Expected all tensors to be on the same device, but got cpu and cuda during forward pass",
            "canonical_fix": "Move each dataloader batch to the model device before the forward pass.",
            "prevention_rule": "Every minibatch transfer must happen before the forward pass.",
            "project_scope": "global",
            "canonical_symptom": "mixed cuda and cpu tensors during forward pass",
            "verification_steps": "Run one dataloader batch through the model.",
            "tags": "pytorch,cuda,cpu,training",
            "error_family": "tensor_device_error",
            "root_cause_class": "tensor_cross_device_mix",
            "command": "python train.py",
            "file_path": "data/dataloader.py",
            "stack_excerpt": 'File "data/dataloader.py", line 18, in move_batch_to_device',
            "repo_name": "vision-trainer",
            "domain": "pytorch",
        },
        expect_same_pattern=True,
        expect_same_variant=False,
    ),
]


def run_merge_correctness_stress(app: Any) -> dict[str, Any]:
    exact_reuse = 0
    safe_split = 0
    catastrophic_variant_merge = 0
    pattern_split_miss = 0
    failures: list[dict[str, Any]] = []

    for case in MERGE_STRESS_CASES:
        first = app.issue_record_resolution(**case.first)
        second = app.issue_record_resolution(**case.second)
        same_pattern = int(first["pattern_id"]) == int(second["pattern_id"])
        same_variant = int(first["variant_id"]) == int(second["variant_id"])
        if case.expect_same_pattern != same_pattern:
            pattern_split_miss += 1
            failures.append({
                "slug": case.slug,
                "expected_same_pattern": case.expect_same_pattern,
                "observed_same_pattern": same_pattern,
                "expected_same_variant": case.expect_same_variant,
                "observed_same_variant": same_variant,
            })
            continue
        if case.expect_same_variant and same_variant:
            exact_reuse += 1
        elif not case.expect_same_variant and not same_variant:
            safe_split += 1
        else:
            catastrophic_variant_merge += 1
            failures.append({
                "slug": case.slug,
                "expected_same_pattern": case.expect_same_pattern,
                "observed_same_pattern": same_pattern,
                "expected_same_variant": case.expect_same_variant,
                "observed_same_variant": same_variant,
            })

    review_queue = app.issue_review_queue(status="pending", limit=50)
    total_cases = len(MERGE_STRESS_CASES)
    return {
        "dataset": "merge_correctness_stress",
        "total_cases": total_cases,
        "exact_variant_reuse_count": exact_reuse,
        "safe_variant_split_count": safe_split,
        "catastrophic_variant_merge_count": catastrophic_variant_merge,
        "pattern_split_miss_count": pattern_split_miss,
        "review_queue_pending": int(review_queue.get("count", 0)),
        "success_rate": round((exact_reuse + safe_split) / max(total_cases, 1), 4),
        "failures": failures[:20],
    }


__all__ = ["MERGE_STRESS_CASES", "MergeStressCase", "run_merge_correctness_stress"]
