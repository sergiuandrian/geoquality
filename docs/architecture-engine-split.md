# Wave 3 design: engine phase split (not implemented yet)

**Status:** design only — implement after Wave 1 (P0 safety) and Wave 2 (P1 honesty).  
**Goal:** make `run_suite` / `_run_layer` readable and extension-safe without changing user-facing YAML semantics.

---

## Problem

`src/geoqa/engine.py` is a single orchestrator (~580 LOC) that owns:

1. suite iteration + thread pool
2. fingerprint cache hit/miss
3. SQL-only vs materialize vs chunk vs full load
4. registry check dispatch
5. repair + file/PostGIS write-back
6. failure GeoJSON collection

Hardcoded sets (`CHUNK_SAFE_CHECKS`, `CHUNK_GLOBAL_CHECKS`, CLI “builtin” names, `only_sql_safe_checks`) drift from the registry. New built-ins and plugins cannot declare how they interact with chunk/SQL/repair.

---

## Target shape

Keep the public API stable:

```python
from geoqa.engine import run_suite  # still the only entry point for library users
```

Internal package layout (new modules under `src/geoqa/engine/` **or** sibling modules — prefer package with re-export):

```text
geoqa/
  engine/
    __init__.py          # re-export run_suite
    suite.py             # run_suite, parallel fan-out, audit flush
    layer.py             # run_layer phase pipeline
    load_strategy.py     # defer / SQL-only / materialize / chunk
    dispatch.py          # registry loop + _timed
    repair_phase.py      # effective_repair → write gates
    cache_phase.py       # keying, hit, write (PostGIS rules live here)
    failures.py          # _collect_failures
  sql_ident.py           # _quote_table, _redact (moved out of datasource)
```

Compatibility shim: keep `geoqa.engine` as a module that imports from `geoqa.engine.suite` **or** turn `engine.py` into the package — hatch/`src` layout must still expose `geoqa.engine.run_suite`. Preferred: replace `engine.py` with `engine/` package and `engine/__init__.py` exporting `run_suite`.

---

## Phase pipeline (per layer)

```text
┌─────────────┐
│ resolve cfg │──ERROR──► return LayerReport
└──────┬──────┘
       ▼
┌─────────────┐
│ cache lookup│──HIT──► SKIP + return
└──────┬──────┘
       ▼
┌──────────────────────────┐
│ choose LoadStrategy      │
│  - sql_only              │  (geometry SQL, no GDF, no repair)
│  - materialize_postgis   │
│  - chunk_file            │  (no repair; global checks SKIP)
│  - full_gdf              │
└──────┬───────────────────┘
       ▼
┌─────────────┐
│ validate    │  registry.dispatch (optional skip geometry if SQL hybrid)
└──────┬──────┘
       ▼
┌─────────────┐
│ repair      │  only full_gdf (and hybrid after materialize)
│  └─ write   │  file | postgis | none  (gates unchanged)
└──────┬──────┘
       ▼
┌─────────────┐
│ re-validate │  OPT-IN later (Wave 2+/1.0): repair.then_recheck
└──────┬──────┘
       ▼
┌─────────────┐
│ failures +  │
│ cache write │
└─────────────┘
```

**Invariant (post Wave 1):** SQL-only is illegal when `geometry.fix` or `geometry.repair.enabled` is set — strategy must be `materialize_postgis` / `full_gdf` so repair can run.

---

## CheckSpec strategy metadata

Extend `CheckSpec` in `registry.py` (defaults preserve today’s built-in behavior):

| Field | Type | Meaning |
|-------|------|---------|
| `chunk_safe` | `bool` | May run per chunk and merge results |
| `requires_full_layer` | `bool` | If true, chunk mode emits SKIP/WARN instead of running |
| `sql_pushdown` | `Literal["none","geometry"]` | Participates in SQL-only / hybrid geometry path |

Built-ins:

| Check | chunk_safe | requires_full_layer | sql_pushdown |
|-------|------------|---------------------|--------------|
| crs | yes | no | none |
| schema | yes* | no | none |
| geometry | yes | no | geometry |
| duplicates | no | yes | none |
| attributes | yes† | unique → yes | none |
| topology | no | yes | none |
| metadata | yes | no | none |

\* schema that needs whole-layer stats stays full-layer if we add such rules later.  
† today unique is stripped in chunk mode — become `requires_full_layer` for that sub-rule or SKIP unique explicitly via metadata.

Delete hardcoded frozensets in `datasource.py` / engine once registry is source of truth. `only_sql_safe_checks` becomes: every *enabled* check either has `sql_pushdown != "none"` or is disabled / empty of work — **including plugins**.

---

## Module responsibilities

### `load_strategy.py`
- Input: `Layer`, `LayerConfig`, suite flags  
- Output: enum + side effects on `layer.gdf` / `layer.error`  
- Owns: `_load_postgis` materialize, `iter_file_chunks` entry, SQL-only eligibility (calls shared helper that respects repair)

### `dispatch.py`
- `run_checks(gdf, layer, cfg, *, skip: set[str]) -> list[CheckResult]`
- Thin wrapper over `get_registry().specs()` + `_timed`

### `repair_phase.py`
- `apply_repair(...)` → update `LayerReport`, audits, file/postgis write
- Status from write result `ok` flag (not substring `"failed"`)

### `cache_phase.py`
- `cache_key(layer, cfg) -> str | None` (`None` = do not cache; PostGIS after Wave 1)
- `try_hit` / `store_if_clean`

### `suite.py`
- `run_suite` orchestration, workers, audit JSON flush only

---

## Migration steps (when coding Wave 3)

1. Extract helpers with **zero behavior change** (move functions, re-export).
2. Introduce `LoadStrategy` enum used by existing branches (still same if/return paths).
3. Add `CheckSpec` flags; dual-read old frozensets + flags for one release.
4. Remove frozensets; update plugin docs (`docs/plugins.md`).
5. Optional: `repair.then_recheck` behind config default `false`.

**Do not** combine with Wave 1/2 bugfixes in the same PR.

---

## Out of scope for Wave 3

- Topology file split (`topology.py` stays one module)
- Live PostGIS write batching / temp-table UPDATE
- Changing default repair-after-checks order (document in Wave 2; recheck is opt-in later)
- PyPI / 1.0 marketing

---

## Success criteria

- `run_suite` public signature unchanged
- All existing unit tests pass without semantic changes
- New unit test: plugin with `requires_full_layer=True` gets SKIP under `chunk_size`
- `engine/` modules each &lt; ~200 LOC; no new circular imports (`config` ↔ `registry` unchanged)
