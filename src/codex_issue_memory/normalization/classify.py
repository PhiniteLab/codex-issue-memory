from __future__ import annotations


def _contains_any(text: str, needles: tuple[str, ...]) -> bool:
    return any(needle in text for needle in needles)


def classify_from_text(text: str) -> tuple[str, str, list[str], list[str]]:
    """Return ``error_family``, ``root_cause_class``, tags, and evidence signals."""
    tags: set[str] = set()
    evidence: list[str] = []
    family = 'generic_runtime_error'
    root = 'unknown'

    if _contains_any(text, ('modulenotfounderror', 'importerror', 'cannot import name', 'cannot import', 'cannot be imported', 'failed to import', 'unable to import', 'no module named')):
        family = 'import_error'
        root = 'missing_python_module'
        tags.update({'python', 'import'})
        evidence.append('import-signals')
    elif _contains_any(
        text,
        (
            'module missing',
            'missing module',
            'module is absent',
            'absent from the current environment',
            'not installed in the current environment',
            'package missing from the environment',
        ),
    ) and _contains_any(text, ('module', 'package', 'environment', 'import')):
        family = 'import_error'
        root = 'missing_python_module'
        tags.update({'python', 'import'})
        evidence.append('import-symptom-signals')

    has_missing_file_signal = _contains_any(
        text,
        (
            'filenotfounderror',
            'no such file or directory',
            'does not exist',
            'not found',
            'missing file',
            'cannot open',
            'could not open',
            'unable to open',
        ),
    )
    if has_missing_file_signal:
        family = 'path_resolution_error'
        root = 'missing_resource_file'
        tags.update({'path', 'filesystem'})
        evidence.append('missing-file-signals')
        if _contains_any(
            text,
            (
                'cwd', 'working directory', 'relative path', 'repo root', 'another directory',
                'external cwd', 'outside the repository', 'outside repo root',
            ),
        ):
            root = 'cwd_relative_path_bug'
            evidence.append('cwd-relative-signals')

    if _contains_any(
        text,
        (
            'depends on cwd',
            'depends on runtime cwd',
            'cwd dependent',
            'started outside the repository root',
            'started outside repository root',
            'outside repo root',
            'outside the repository root',
            'repository root',
            'another working directory',
            'nested subdirectory',
            'runtime cwd',
        ),
    ) and _contains_any(text, ('path', 'cwd', 'directory', 'repo root', 'repository root', 'subdirectory', 'working directory')):
        family = 'path_resolution_error'
        root = 'cwd_relative_path_bug'
        tags.update({'path', 'filesystem'})
        evidence.append('cwd-relative-symptom-signals')

    if _contains_any(text, ('sqlite', '.sqlite3', 'database')):
        tags.update({'sqlite', 'database'})
        if _contains_any(text, ('database is locked', 'locked')):
            family = 'sqlite_error'
            root = 'database_locked'
            evidence.append('sqlite-locked-signals')
        elif _contains_any(text, ('no such table', 'no such column')):
            family = 'sqlite_error'
            root = 'missing_table_or_column'
            evidence.append('sqlite-schema-signals')
        elif family == 'path_resolution_error':
            family = 'sqlite_error'
            if root == 'missing_resource_file':
                root = 'sqlite_db_path_error'
            evidence.append('sqlite-path-signals')
        else:
            family = 'sqlite_error'
            root = root if root != 'unknown' else 'sqlite_runtime_error'
            evidence.append('sqlite-signals')

    if _contains_any(text, ('database lock', 'sqlite database', 'concurrent writers', 'writer access', 'lock the sqlite database')):
        family = 'sqlite_error'
        root = 'database_locked'
        tags.update({'sqlite', 'database'})
        evidence.append('sqlite-lock-symptom-signals')

    has_config_container = _contains_any(text, ('yaml', 'toml', 'config', 'configuration', 'settings', '.env', 'keyerror', 'schema'))
    has_config_key_signal = _contains_any(text, ('missing key', 'invalid key', 'required key', 'keyerror')) or (
        _contains_any(text, ('not found in', 'absent from'))
        and _contains_any(text, ('yaml', 'toml', 'config', 'configuration', 'settings'))
    ) or (
        _contains_any(text, ('missing ', 'misses ', 'lacks ', 'without '))
        and _contains_any(text, ('yaml', 'toml', 'config', 'configuration', 'settings', 'schema', 'mixer'))
    )
    if has_config_container and has_config_key_signal:
        family = 'config_error'
        root = 'invalid_config_key'
        tags.update({'config'})
        evidence.append('config-signals')

    if _contains_any(text, ('permission denied', 'operation not permitted', 'access is denied')):
        family = 'permission_error'
        root = 'insufficient_permissions'
        tags.update({'permissions'})
        evidence.append('permission-signals')

    if _contains_any(text, ('not writable', 'write permissions', 'protected directory', 'read only directory')) and _contains_any(
        text,
        ('directory', 'output', 'export', 'write', 'writable'),
    ):
        family = 'permission_error'
        root = 'insufficient_permissions'
        tags.update({'permissions'})
        evidence.append('permission-symptom-signals')

    if _contains_any(text, ('unrecognized arguments', 'usage:', 'invalid choice', 'too few arguments', 'argument parser')):
        family = 'cli_usage_error'
        root = 'invalid_cli_argument'
        tags.update({'cli'})
        evidence.append('cli-signals')

    if _contains_any(text, ('unicodedecodeerror', 'unicodeencodeerror', "codec can't decode", "codec can't encode")):
        family = 'encoding_error'
        root = 'unicode_codec_mismatch'
        tags.update({'encoding', 'unicode'})
        evidence.append('encoding-signals')

    if _contains_any(text, ('wrong unicode codec', 'wrong codec', 'wrong encoding', 'codec mismatch')) and _contains_any(
        text,
        ('unicode', 'codec', 'encoding', 'utf-8', 'utf8'),
    ):
        family = 'encoding_error'
        root = 'unicode_codec_mismatch'
        tags.update({'encoding', 'unicode'})
        evidence.append('encoding-symptom-signals')

    if _contains_any(text, ('keyerror', 'column', 'columns', 'dataframe')) and _contains_any(
        text,
        ('pandas', 'dataframe', 'column', 'columns', 'excel', 'xlsx', 'sheet'),
    ):
        family = 'pandas_schema_error'
        root = 'dataframe_column_mismatch'
        tags.update({'pandas', 'schema'})
        evidence.append('pandas-signals')
        if _contains_any(text, ('excel', 'xlsx', 'worksheet', 'sheet')):
            family = 'excel_header_mapping_error'
            root = 'excel_header_normalization_missing'
            tags.update({'excel'})
            evidence.append('excel-signals')

    if _contains_any(text, ('not json serializable', 'jsondecodeerror', 'json decode error', 'expecting value')):
        family = 'json_error'
        root = 'serialization_contract_mismatch'
        tags.update({'json'})
        evidence.append('json-signals')

    if _contains_any(text, ('malformed json', 'json serialization', 'serialized chunk metadata', 'deserialize', 'deserialise')) and _contains_any(
        text,
        ('json', 'metadata', 'serialization', 'serialize', 'deserialize', 'chunk'),
    ):
        family = 'json_error'
        root = 'serialization_contract_mismatch'
        tags.update({'json'})
        evidence.append('json-symptom-signals')

    if _contains_any(
        text,
        ('connection refused', 'timed out', 'timeout', 'name or service not known', 'temporary failure in name resolution', 'max retries exceeded'),
    ):
        family = 'network_error'
        root = 'endpoint_unreachable'
        tags.update({'network'})
        evidence.append('network-signals')

    if _contains_any(text, ('401', '403', 'unauthorized', 'forbidden', 'invalid token', 'invalid api key', 'access token')):
        family = 'auth_error'
        root = 'invalid_credentials'
        tags.update({'auth'})
        evidence.append('auth-signals')

    if _contains_any(text, ('publickey', 'ssh', 'git remote')) and _contains_any(text, ('permission denied', 'auth', 'authentication')):
        family = 'auth_error'
        root = 'ssh_publickey_auth_failure'
        tags.update({'auth'})
        evidence.append('ssh-auth-signals')

    if _contains_any(text, ('assertionerror', 'assert ', 'expected', 'but got')) and family == 'generic_runtime_error':
        family = 'test_assertion_failure'
        root = 'assertion_contract_violation'
        tags.update({'testing'})
        evidence.append('assertion-signals')

    if _contains_any(
        text,
        (
            'mat1 and mat2 shapes cannot be multiplied', 'size mismatch', 'shape mismatch',
            'invalid shape', 'dimension out of range', 'expected input', 'rank mismatch',
            'rank mismatched', 'dimension mismatch', 'inconsistent dimensions',
        ),
    ):
        family = 'tensor_shape_error'
        root = 'tensor_rank_or_dim_mismatch'
        tags.update({'pytorch', 'tensor', 'shape'})
        evidence.append('tensor-shape-signals')

    if _contains_any(text, ('index dimension', 'embedding dimension', 'block matrices', 'horizon matrices')) and _contains_any(
        text,
        ('dimension', 'mismatch', 'inconsistent', 'rank', 'matrix', 'matrices'),
    ):
        family = 'tensor_shape_error'
        root = 'tensor_rank_or_dim_mismatch'
        tags.update({'pytorch', 'tensor', 'shape'})
        evidence.append('tensor-shape-symptom-signals')

    if _contains_any(text, ('lora', 'adapter', 'checkpoint', 'base model')) and _contains_any(
        text,
        ('rank', 'size mismatch', 'base model differs', 'shape mismatch', 'resume adapter checkpoint'),
    ):
        family = 'tensor_shape_error'
        root = 'tensor_rank_or_dim_mismatch'
        tags.update({'pytorch', 'tensor', 'shape'})
        evidence.append('lora-shape-symptom-signals')

    if _contains_any(text, ('expected all tensors to be on the same device', 'cuda', 'cpu', 'device-side assert')) and _contains_any(
        text,
        ('cuda', 'cpu', 'device'),
    ):
        family = 'tensor_device_error'
        root = 'tensor_cross_device_mix'
        tags.update({'pytorch', 'tensor', 'device'})
        evidence.append('tensor-device-signals')

    if _contains_any(text, ('cuda driver version is insufficient for cuda runtime version', 'driver version is insufficient')):
        family = 'environment_error'
        root = 'cuda_runtime_compatibility'
        tags.update({'environment', 'cuda'})
        evidence.append('cuda-runtime-compatibility-signals')

    if _contains_any(
        text,
        (
            'expected scalar type',
            'dtype mismatch',
            'wrong dtype',
            'torch.float',
            'float32',
            'float64',
            'found double',
            'found longtensor',
            'tensor is float64',
        ),
    ) or (
        _contains_any(text, ('dtype', 'double', 'float', 'longtensor'))
        and _contains_any(text, ('tensor', 'torch', 'pytorch', 'scalar type'))
    ):
        family = 'tensor_dtype_error'
        root = 'tensor_dtype_mismatch'
        tags.update({'pytorch', 'tensor', 'dtype'})
        evidence.append('tensor-dtype-signals')

    if _contains_any(text, ('out of memory', 'cuda out of memory', 'oom', 'killed process')):
        family = 'oom_memory_error'
        root = 'memory_budget_exceeded'
        tags.update({'memory'})
        evidence.append('oom-signals')

    if _contains_any(text, ('nan', 'inf', 'overflow', 'underflow')) and _contains_any(
        text,
        ('loss', 'grad', 'gradient', 'optimizer', 'training'),
    ):
        family = 'numerical_stability_error'
        root = 'nan_or_inf_instability'
        tags.update({'numerics'})
        evidence.append('numeric-instability-signals')

    if _contains_any(text, ('env var', 'environment variable', 'not set', 'keyerror')) and _contains_any(
        text,
        ('dotenv', '.env', 'os.environ', 'environment variable', 'not set', 'api key', 'openai', 'secret', 'secrets', 'prompt', 'eval_prompts'),
    ):
        family = 'environment_error'
        root = 'missing_env_var'
        tags.update({'environment'})
        evidence.append('env-signals')

    if _contains_any(text, ('env var is absent', 'env var missing', 'api key env var', 'required api key env var')) and _contains_any(
        text,
        ('api key', 'env var', 'environment', 'not set', 'absent', 'missing'),
    ):
        family = 'environment_error'
        root = 'missing_env_var'
        tags.update({'environment'})
        evidence.append('env-symptom-signals')

    return family, root, sorted(tags), evidence


_classify_from_text = classify_from_text
