# npd_fhir_loader → css-catalog-client migration (plan 3) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace npd_fhir_loader's own catalog code (`CssCatalogPg`, `tests/fakes.py`) with the `css_catalog` package
from css-catalog-client, with the loader's behavior unchanged, and add SQL Server as a selectable catalog backend.

**Architecture:** `npd_loader/catalog.py` keeps NPD's own `Catalog` protocol, `Run` dataclass and NPD-only logic
(`release_date` from `xml_config`, `newest_run`, `releases_of`, the meaning of `source_version_num` as the release
date). It becomes a thin adapter, `NpdCatalog`, over any `css_catalog.Catalog`. Stage code (`stages.py`, `download.py`,
`extract.py`, `import_stage.py`, `retention.py`) does not change. `FakeCatalog` becomes `NpdCatalog` over
`css_catalog.MemoryCatalog`. A row snapshot taken before the change proves the catalog rows are unchanged afterwards.

**Tech Stack:** Python ≥ 3.12, `css_catalog` (css-catalog-client `python/`), psycopg 3, pyodbc (SQL Server only),
pytest.

**Spec:** `docs/superpowers/specs/2026-10-22-css-catalog-client-design.md` (copy of the css-catalog-client spec,
refreshed 2026-10-25). The section "NPD loader migration" is the requirement. Also read the library's contract:
`C:\Users\eellsworth\Documents\python_projects\css-catalog-client\spec\contract.md`.

This is plan 3 of 4. Plan 1 (the Python package) is done, on css-catalog-client branch `feat/python-package`.

## Global Constraints

- **Branch and commits (owner's decision):** all changes go on npd_fhir_loader branch `css-catalog-client-migration`
  and stay **uncommitted**. There are no commit steps in this plan. Never commit, stash, reset or switch branches.
  Leave the owner's untracked `docs/run-sequence.md` alone.
- **Behavior unchanged:** the same run rows, file rows and file names, and the same `npd-loader status` output.
  The test of this is the row snapshot (Task 1). It matches field for field, except ids and timestamps.
- **Tests:** existing tests may be edited **only** where they name `CssCatalogPg`, construct the catalog, or import
  from a moved path. No assertion changes. New tests may be added.
- **Stage code doesn't change:** `stages.py`, `download.py`, `extract.py`, `import_stage.py` and `retention.py` keep
  calling `start_run(run_class, description, config_xml)`, `finish_run`, `add_data_file`, `update_data_file`,
  `get_data_files`, `last_successful_run` and `successful_releases` exactly as today.
- **Timestamps:** `date_started` and `date_completed` stay client-set, as `datetime.now().replace(microsecond=0)`.
  Pass `started_at=` / `completed_at=` explicitly. Never rely on the database's "now".
- **Fixed field set:** NPD callers must not be able to set `file_set` or `source_version_name`. They come from
  `[catalog]` config, as today. Any other unknown field raises `TypeError("unknown data_file fields: [...]")`.
- **Explicit table names:** always pass `run_table` / `file_table` to the library. `css_catalog.connect()` otherwise
  reads `CSS_CATALOG_RUN_TABLE` / `CSS_CATALOG_FILE_TABLE` from the environment, and NPD must not pick those up by
  accident.
- **Dependency name:** never add a bare `css-catalog` requirement that pip would look up on PyPI. Someone else could
  own that name there. Use only the local editable install (Task 0) or the GitHub direct reference (Task 4).
- **Test servers:** Postgres is the portable server in `local_pg`. Set `NPD_TEST_PG_DSN=postgresql://test:test@127.0.0.1:55432/postgres`.
  SQL Server (only for Task 3's optional live check) is
  `CSS_TEST_MSSQL_ODBC=Driver={ODBC Driver 18 for SQL Server};Server=127.0.0.1,1433;UID=sa;PWD=<sa password>;Database=master;TrustServerCertificate=yes`.
  Use `127.0.0.1`, never `localhost`.

## Review Focus

1. **A run row from another project, or with another `run_type`, carrying the same `run_class`** must not count as
   NPD's last successful run. That needs `find_runs(project=…, run_type=…)`. Pinned by the existing
   `test_other_projects_runs_are_ignored`, which must pass unchanged.
2. **A `result` longer than 2000 characters, or `exceptions` longer than 8000,** must still be truncated, not
   rejected. Pinned by the existing `test_writes_configured_labels` and `test_exceptions_truncated`.
3. **Custom status labels** (`status_success = "OK"` in config) must still be written and read back as SUCCESS.
   Pinned by the new `test_custom_status_labels_round_trip` (Task 2).
4. **An `xml_config` with no `release_date`, or unparseable XML,** must give `Run.release_date = None` and be skipped
   by `last_successful_run`, not crash. Pinned by the new `test_run_without_release_is_ignored` (Task 2).
5. **A config with `backend = "mssql"` but Postgres-style `host`/`port`/`dbname`, or with no `odbc`,** must fail at
   config load with a clear `ConfigError`, not at the first catalog call. Pinned by the new config tests in Task 3.

---

## File structure

```
src/npd_loader/catalog.py      rewritten: NpdCatalog adapter + open_catalog(); Run, protocol and NPD helpers kept
src/npd_loader/config.py       CATALOG_BACKENDS += "mssql"; [catalog] odbc; per-backend table defaults
src/npd_loader/cli.py          build_context uses open_catalog()
tests/fakes.py                 FakeCatalog = NpdCatalog over css_catalog.MemoryCatalog + test helpers
tests/test_catalog_rows.py     NEW: row snapshot test
tests/data/catalog_rows_snapshot.json  NEW: snapshot taken before the change
tests/test_catalog_pg.py, tests/test_e2e.py   constructor lines only
tests/test_config.py           new mssql cases
config.example.toml, README.md, pyproject.toml
```

---

### Task 0: Install css_catalog and record the baseline

**Files:** none (environment only)

- [ ] **Step 1: Start Postgres.** `local_pg\pgsql\bin\pg_ctl.exe -D local_pg\pgdata -l local_pg\pg.log start` (it may
  already be running). Then confirm that `git branch --show-current` prints `css-catalog-client-migration`.
- [ ] **Step 2: Run the baseline suite before touching anything.** Set `$env:NPD_TEST_PG_DSN="postgresql://test:test@127.0.0.1:55432/postgres"`,
  then run `.venv\Scripts\python.exe -m pytest -q -rs`. Expected: all pass, none skipped. Record the counts in the
  task report.
- [ ] **Step 3: Install the library editable from the local checkout.**
  `.venv\Scripts\python.exe -m pip install -e "..\css-catalog-client\python[mssql]"`. Then
  `.venv\Scripts\python.exe -c "import css_catalog; print(css_catalog.__file__)"` should print a path under
  `css-catalog-client\python\src`. Don't edit `pyproject.toml` yet (Task 4).

---

### Task 1: Row snapshot taken before the change

**Files:**
- Create: `tests/test_catalog_rows.py`, `tests/data/catalog_rows_snapshot.json`

**Interfaces:**
- Produces: `normalized_rows(catalog_conninfo: str) -> dict` in `tests/test_catalog_rows.py`. It gives
  `{"runs": [...], "files": [...]}` with every column of both tables except `id`, `run_id` (uuid), `date_started`,
  `date_completed`, `date_loaded`, `date_modified` and `date_created`. Each run's integer `id` becomes its 0-based
  position in id order (`"#0"`, `"#1"`…), and each file's `run_id` and `parent_file` are remapped to those positions.
  File names are made comparable by replacing `file_<digits>_` with `file_<pos>_`, where `<pos>` is the file's
  position. `xml_config` / `xml_output` are compared as text with any embedded file or run ids remapped the same way.
  Rows are sorted by original id.

The test drives the same flow as `test_e2e.py`'s first release: the fake CMS, then download, extract and import for
one release, against `catalog_db` (Postgres DDL from `tests/sql/css_catalog_schema.sql`) and `npd_db`. Reuse
`test_e2e.py`'s setup helpers instead of duplicating them.

- [ ] **Step 1: Write `test_catalog_rows_match_snapshot`.** If `tests/data/catalog_rows_snapshot.json` doesn't exist,
  and only when the env var `NPD_WRITE_CATALOG_SNAPSHOT=1` is set, write it and `pytest.skip("snapshot written")`.
  Otherwise assert `normalized_rows(catalog_db) == json.load(snapshot)`.
- [ ] **Step 2: Take the snapshot on the unchanged code.** Run
  `$env:NPD_WRITE_CATALOG_SNAPSHOT="1"; .venv\Scripts\python.exe -m pytest tests/test_catalog_rows.py -q -rs`, then
  `Remove-Item Env:NPD_WRITE_CATALOG_SNAPSHOT`. Open the JSON and check that it holds runs for DOWNLOAD, EXTRACT and
  IMPORT with project `NPD`, plus manifest, `ndjson.zst` and `ndjson` files with `file_set` `NPD_FHIR`.
- [ ] **Step 3: Check the test passes on unchanged code.** `.venv\Scripts\python.exe -m pytest tests/test_catalog_rows.py -v`.
  Expected: PASS. Run it twice to show the normalization is stable.

---

### Task 2: NpdCatalog adapter and FakeCatalog over MemoryCatalog

**Files:**
- Modify: `src/npd_loader/catalog.py` (rewrite), `tests/fakes.py` (rewrite)
- Modify (constructor lines only): `tests/test_catalog_pg.py`, `tests/test_e2e.py`
- Test: `tests/test_catalog_pg.py` (two new tests), `tests/test_catalog_rows.py` (from Task 1)

**Interfaces:**
- Keeps, with unchanged names and behavior, in `npd_loader.catalog`: `SUCCESS`, `FAILED`, `Run` (same fields:
  `id, run_class, description, config_xml, started_at, release_date, status`), the `Catalog` Protocol (same seven
  methods and signatures), `newest_run`, `releases_of`.
- `DataFile` is re-exported from `css_catalog`. It is a superset of the old one: it adds `file_set` and
  `source_version_name`, with the same attribute names otherwise.
- Removes from `npd_loader.catalog`: `CssCatalogPg`, `DATA_FILE_FIELDS`, `clean_fields`, `truncate`, `MAX_RESULT`,
  `MAX_EXCEPTIONS`, `_qualified`. Truncation now happens in the library.
- Produces:
  - `NPD_FILE_FIELDS: tuple[str, ...] = tuple(f for f in css_catalog.DATA_FILE_FIELDS if f not in ("file_set", "source_version_name"))`
  - `class NpdCatalog` with `__init__(self, inner: css_catalog.Catalog, cfg: CatalogConfig)`. It implements NPD's
    `Catalog` Protocol:
    - `start_run(run_class, description, config_xml)` calls `inner.start_run(project=cfg.project, run_type=cfg.run_type,
      run_class=run_class, description=description, config=config_xml, started_at=datetime.now().replace(microsecond=0))`
      and returns NPD's `Run` (status `None`, `release_date=parse_release(config_xml)`).
    - `finish_run(run, status, result=None, output_xml=None)` calls `inner.finish_run(run.id, status, result=result,
      output_xml=output_xml, completed_at=datetime.now().replace(microsecond=0))`.
    - `add_data_file(run, **fields)` rejects keys not in `NPD_FILE_FIELDS` with
      `TypeError(f"unknown data_file fields: {sorted(unknown)}")`, then calls `inner.add_file(run.id,
      file_set=cfg.file_set, source_version_name=cfg.source_version_name, **fields)`.
    - `update_data_file(file_id, **fields)` uses the same `NPD_FILE_FIELDS` check, then `inner.update_file`. An empty
      `fields` returns without calling the library, as today.
    - `get_data_files(release, file_type, run_id=None)` returns
      `inner.find_files(file_set=cfg.file_set, source_version_num=release.isoformat(), file_type=file_type, run_id=run_id)`.
    - `last_successful_run(run_class, release=None)` and `successful_releases(run_class)` build on
      `inner.find_runs(project=cfg.project, run_type=cfg.run_type, run_class=run_class, status=SUCCESS)`. Each library
      run maps to NPD `Run(id, run_class, description or "", config_xml or "", started_at,
      parse_release(config_xml), SUCCESS)`, and the result goes through `newest_run` / `releases_of`.
  - `open_catalog(cfg: CatalogConfig, connection: str) -> NpdCatalog`. It calls `css_catalog.connect(backend,
    connection, run_table=cfg.run_table, file_table=cfg.file_table, status_success=cfg.status_success,
    status_failed=cfg.status_failed)`, where the library backend is `"postgres"` for `css_catalog_pg` and `"mssql"`
    for `mssql`.
- Produces `tests/fakes.py`: `class FakeCatalog(NpdCatalog)` with `__init__(self, cfg: CatalogConfig | None = None)`.
  It wraps `MemoryCatalog(status_success=cfg.status_success, status_failed=cfg.status_failed, first_run_id=62000,
  first_file_id=12000)`. With no `cfg`, it uses `parse_config(config_data("/tmp", "https://x/manifest.json")).catalog`
  from `tests/helpers.py` (move that import inside the function if `helpers` imports `fakes`). It exposes
  `runs` / `files` as properties returning the `MemoryCatalog`'s live dicts. Stage tests read `runs[id]["status" |
  "run_class" | "description" | "config_xml" | "result" | "output_xml"]` and set `runs[id]["status"] = FAILED`; with
  default labels the stored label equals the logical status. It keeps `add_successful_run(run_class, release) -> Run`,
  which does `start_run(run_class, "test", build_config_xml({"release_date": release.isoformat()}))` and then
  `finish_run(run, SUCCESS)`.

- [ ] **Step 1: Write the new tests in `tests/test_catalog_pg.py`.**
  - `test_custom_status_labels_round_trip`: build `catalog_config()` with `status_success="OK"` and
    `status_failed="Bad"` (use `dataclasses.replace`), then `open_catalog(cfg, catalog_db)`. Start and finish a run
    with SUCCESS, which stores `completion_status = 'OK'` (check with raw SQL). Then
    `last_successful_run("DOWNLOAD").id == run.id`.
  - `test_run_without_release_is_ignored`: start runs with config `build_config_xml({"run_type": "x"})` and with
    `"<oops"`, and finish both with SUCCESS. Then `last_successful_run("DOWNLOAD") is None` and
    `successful_releases("DOWNLOAD") == []`.
- [ ] **Step 2: Change only the construction lines.**
  - `tests/test_catalog_pg.py`: `CssCatalogPg(catalog_db, catalog_config())` becomes
    `open_catalog(catalog_config(), catalog_db)`, with the import updated.
  - `tests/test_e2e.py:33`: becomes `open_catalog(parse_config(data).catalog, catalog_db)`.
  - Nothing else in either file changes.
- [ ] **Step 3: Run the tests and watch them fail.** `.venv\Scripts\python.exe -m pytest tests/test_catalog_pg.py -q`.
  Expected: ImportError (`open_catalog`).
- [ ] **Step 4: Rewrite `src/npd_loader/catalog.py` and `tests/fakes.py`, and change `cli.py`'s import.** `cli.py`
  imports `CssCatalogPg`, so change `build_context` here to `open_catalog(config.catalog, conninfo(config, "catalog"))`.
  Task 3 replaces `conninfo(...)` with `catalog_connection(config)`.
- [ ] **Step 5: Run the full suite.** Set `NPD_TEST_PG_DSN`, then `.venv\Scripts\python.exe -m pytest -q -rs`. Expected:
  every baseline test passes, plus the two new ones, plus `test_catalog_rows_match_snapshot`. That last one is the
  behavior-unchanged proof; if it fails, fix the adapter, never the snapshot. No skips.
- [ ] **Step 6: Check that nothing else references the removed names.** Run
  `git grep -n "CssCatalogPg\|clean_fields\|DATA_FILE_FIELDS\|MAX_RESULT" -- src tests`. Expected: no matches, except
  `css_catalog.DATA_FILE_FIELDS` inside `catalog.py`.

---

### Task 3: SQL Server as a catalog backend in config

**Files:**
- Modify: `src/npd_loader/config.py`, `src/npd_loader/cli.py` (if not already done in Task 2), `config.example.toml`,
  `README.md`
- Test: `tests/test_config.py`, `tests/test_cli.py`

**Interfaces:**
- `CATALOG_BACKENDS = {"css_catalog_pg", "mssql"}`.
- `CatalogConfig` gains `odbc: str | None = None`. Its `host`, `port` and `dbname` become `str | None` / `int | None`
  with default `None`, so the dataclass field order must keep non-defaults first. Their types and defaults change, but
  the values for Postgres configs don't.
- Rules in `parse_config`:
  - `css_catalog_pg`: `host`, `port` and `dbname` are required, as today, and `odbc` must be absent.
  - `mssql`: `odbc` is required (the full ODBC connection string), and `host`, `port`, `dbname`, `user` and `password`
    must be absent. The SQL Server login comes from the ODBC string, normally `Trusted_Connection=yes`. Either
    violation raises `ConfigError("[catalog] …")` naming the field.
  - `run_table` / `file_table` default to `public.master_warehouse_run` / `public.data_file` for `css_catalog_pg`,
    and to `dbo.MASTER_WAREHOUSE_RUN` / `dbo.DATA_FILE` for `mssql`, unless they're given. Existing Postgres configs
    and tests keep their current values.
- `catalog_connection(config: Config) -> str` in `credentials.py`: Postgres gives `conninfo(config, "catalog")`, and
  mssql gives `config.catalog.odbc`. `cli.build_context` uses `open_catalog(config.catalog, catalog_connection(config))`.

- [ ] **Step 1: Write the failing tests** in `tests/test_config.py`:
  - `test_mssql_catalog_config`: a `[catalog]` with `backend="mssql"` and an `odbc` string parses. It has
    `run_table == "dbo.MASTER_WAREHOUSE_RUN"` and `file_table == "dbo.DATA_FILE"`, and
    `catalog_connection(cfg) == odbc`.
  - `test_mssql_requires_odbc`: `ConfigError` matching `r"\[catalog\] odbc"`.
  - `test_mssql_rejects_pg_fields`: `host` present gives `ConfigError` matching `r"\[catalog\] host"`.
  - `test_pg_rejects_odbc`
  - `test_mssql_explicit_tables_win`
  - In `tests/test_cli.py`, `test_build_context_mssql_catalog`: `build_context` on an mssql config returns a context
    whose `catalog` is an `NpdCatalog` wrapping a `css_catalog.MssqlCatalog`. Nothing connects.
- [ ] **Step 2: Run the tests and watch them fail.** `.venv\Scripts\python.exe -m pytest tests/test_config.py tests/test_cli.py -q`.
- [ ] **Step 3: Implement** the config rules, `catalog_connection`, and the `build_context` change.
- [ ] **Step 4: Optional live check, only if `CSS_TEST_MSSQL_ODBC` is set.** Add `test_npd_catalog_mssql_round_trip`
  in `tests/test_catalog_pg.py`, or a new `tests/test_catalog_mssql.py` that reuses css-catalog-client's
  `spec/ddl/mssql.sql` with a temporary schema. It runs `start_run` → `add_data_file` → `finish_run` →
  `last_successful_run` through `open_catalog` with `backend="mssql"`, and skips with a reason when the env var is
  unset. Never point it at `HIE_WAREHOUSE_META`.
- [ ] **Step 5: Update `config.example.toml`.** Add a commented SQL Server alternative under `[catalog]`:
  `# backend = "mssql"` / `# odbc = "Driver={ODBC Driver 18 for SQL Server};Server=cssnpi;Database=HIE_WAREHOUSE_META;Trusted_Connection=yes;TrustServerCertificate=yes"`.
  Then update README's config section: one paragraph on the two backends, and the note that ODBC Driver 18 needs
  `TrustServerCertificate=yes` or a trusted certificate.
- [ ] **Step 6: Run the full suite.** `.venv\Scripts\python.exe -m pytest -q -rs`. Expected: all pass, including the
  snapshot test. Any skip must be only the optional SQL Server live check, and only when `CSS_TEST_MSSQL_ODBC` is unset.

---

### Task 4: Declare the dependency (gated on a pushed, tagged library)

**Files:**
- Modify: `pyproject.toml`, `README.md`

**Gate:** this needs css-catalog-client pushed to GitHub with tag `v0.1.0`. Check
`git ls-remote --tags https://github.com/Consumers-Checkbook/css-catalog-client v0.1.0`. If the repo isn't pushed and tagged
yet, **stop and report**. Leave the editable install from Task 0 in
place, and do not add a placeholder requirement.

- [ ] **Step 1: Add the dependency to `pyproject.toml`** `dependencies`:
  `"css-catalog[mssql] @ git+https://github.com/Consumers-Checkbook/css-catalog-client@v0.1.0#subdirectory=python"`, with the
  real owner. Drop `[mssql]` if the owner wants pyodbc optional for NPD; ask in the report rather than guess.
- [ ] **Step 2: Update the README install steps** for the server at 192.10.0.7. The GitHub repo is private, so `pip
  install /opt/npd-loader/src` now needs a GitHub token on that server, for example a read-only fine-grained PAT used
  through `git config --global credential.helper store`, or a deploy key with an SSH URL. Spell out the commands, and
  point to css-catalog-client's `python/README.md` for the token setup. Also cover a Windows development install.
- [ ] **Step 3: Check a clean install.** Create a throwaway venv, run `pip install -e ".[test]"` from npd_fhir_loader,
  then `python -c "import css_catalog, npd_loader.catalog"`. Run the full suite in that venv. Expected: all pass.
