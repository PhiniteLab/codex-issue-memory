"""Phase 4.2 — Technical synonym expansion for improved retrieval recall.

Provides bidirectional synonym mappings and an expansion function that
augments token lists with known equivalents.
"""

from __future__ import annotations

# Bidirectional synonym pairs  (term_a, term_b)
# grouped by domain for readability.
_SYNONYM_PAIRS: list[tuple[str, str]] = [
    # ----- Import / Module -----
    ("missing", "not_found"),
    ("import", "module"),
    ("import", "package"),
    ("module", "package"),
    ("modulenotfounderror", "importerror"),
    ("no_module", "import_error"),
    ("unresolved", "missing"),
    # ----- Dependency -----
    ("dependency", "requirement"),
    ("install", "pip"),
    ("install", "setup"),
    ("version", "compatibility"),
    ("version", "constraint"),
    ("upgrade", "update"),
    ("downgrade", "rollback"),
    ("lockfile", "requirements"),
    # ----- File / Path -----
    ("directory", "folder"),
    ("filename", "filepath"),
    ("path", "location"),
    ("permission", "access"),
    ("permission", "denied"),
    ("readonly", "permission"),
    # ----- Type / Value -----
    ("typeerror", "type_mismatch"),
    ("valueerror", "invalid_value"),
    ("attributeerror", "no_attribute"),
    ("keyerror", "missing_key"),
    ("indexerror", "out_of_range"),
    ("overflow", "out_of_range"),
    # ----- Network -----
    ("timeout", "timed_out"),
    ("connection", "network"),
    ("refused", "unreachable"),
    ("dns", "resolve"),
    ("ssl", "certificate"),
    ("http", "request"),
    # ----- Memory / Resource -----
    ("oom", "out_of_memory"),
    ("memory", "ram"),
    ("leak", "exhausted"),
    ("segfault", "segmentation"),
    ("crash", "abort"),
    # ----- GPU / Tensor -----
    ("gpu", "cuda"),
    ("cuda", "device"),
    ("tensor", "ndarray"),
    ("dtype", "datatype"),
    ("shape", "dimension"),
    ("broadcast", "reshape"),
    ("float32", "fp32"),
    ("float16", "fp16"),
    # ----- Config / Env -----
    ("config", "configuration"),
    ("env", "environment"),
    ("variable", "setting"),
    ("parameter", "argument"),
    ("flag", "option"),
    # ----- Build / Compile -----
    ("compile", "build"),
    ("linker", "linking"),
    ("syntax", "parse"),
    ("undefined", "undeclared"),
    # ----- Database -----
    ("database", "db"),
    ("query", "sql"),
    ("deadlock", "lock"),
    ("constraint", "violation"),
    # ----- Auth -----
    ("auth", "authentication"),
    ("authorize", "permission"),
    ("token", "credential"),
    ("expired", "invalid"),
    # ----- Process -----
    ("process", "pid"),
    ("signal", "sigterm"),
    ("killed", "terminated"),
    ("zombie", "orphan"),
    # ----- Serialization -----
    ("json", "serialize"),
    ("yaml", "config"),
    ("decode", "deserialize"),
    ("encode", "serialize"),
    ("parse", "decode"),
    # ----- Testing -----
    ("assert", "assertion"),
    ("expected", "actual"),
    ("mock", "stub"),
    # ----- General -----
    ("deprecated", "obsolete"),
    ("invalid", "malformed"),
    ("corrupt", "damaged"),
    ("retry", "backoff"),
    ("async", "await"),
    ("concurrent", "parallel"),
    ("race", "concurrent"),
    ("thread", "concurrent"),
]


def _build_synonym_map() -> dict[str, set[str]]:
    """Build bidirectional lookup: token → {all synonyms}."""
    mapping: dict[str, set[str]] = {}
    for a, b in _SYNONYM_PAIRS:
        mapping.setdefault(a, set()).add(b)
        mapping.setdefault(b, set()).add(a)
    return mapping


SYNONYM_MAP: dict[str, set[str]] = _build_synonym_map()


def expand_synonyms(tokens: list[str]) -> list[str]:
    """Return the original tokens plus any known synonyms (deduplicated, order-preserving).

    Synonym tokens are appended after the originals to preserve the natural ordering.
    """
    seen: set[str] = set(tokens)
    expanded = list(tokens)
    for token in tokens:
        for syn in sorted(SYNONYM_MAP.get(token, ())):
            if syn not in seen:
                expanded.append(syn)
                seen.add(syn)
    return expanded


def synonym_pairs_for(token: str) -> list[tuple[str, str]]:
    """Return all (token, synonym) pairs for dense embedding augmentation."""
    synonyms = SYNONYM_MAP.get(token, set())
    return [(token, syn) for syn in sorted(synonyms)]
