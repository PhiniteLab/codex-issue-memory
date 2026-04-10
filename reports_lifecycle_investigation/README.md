# Lifecycle Investigation Reports

This directory contains internal engineering investigation artifacts
from the **owner-key singleton and lifecycle management** work.

These reports document the process of:

1. Tracing the MCP server launch and stdio blocking path
2. Analyzing lock mechanisms and slot management
3. Identifying lifecycle breakpoints and root cause
4. Designing the parent-scoped singleton behavior
5. Implementing and validating the patch

## Report index

| File | Subject |
| --- | --- |
| `00_execution_trace.md` | MCP server launch and runtime path reconstruction |
| `01_lock_analysis.md` | Lock inventory and ownership semantics |
| `02_lifecycle_breakpoints.md` | Lifecycle breakpoints and identity collapse |
| `03_root_cause_proof.md` | Root cause proof for duplicate spawn behavior |
| `04_design_spec.md` | Design specification for parent-scoped singleton |
| `05_patch_map.md` | Patch map and file-level change plan |
| `06_lifecycle_changes.md` | Effective behavior after patch |
| `07_test_results.md` | Test and validation results |
| `08_runtime_proof.md` | Controlled runtime proof of duplicate rejection |

## Status

These reports are **historical reference** material.
The fixes they describe are already merged and covered by `tests/test_server_lifecycle.py`.
