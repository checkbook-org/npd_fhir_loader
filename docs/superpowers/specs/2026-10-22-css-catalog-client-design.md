# css-catalog-client: a shared Python + R catalog library — design

Date: 2026-10-22. Status: approved 2026-10-23; amended 2026-10-25 (config matching, XML equivalence, R uses xml2).

Relative source paths such as `src/npd_loader/catalog.py` and `tests/sql/css_catalog_schema.sql` refer to the
`npd_fhir_loader` repo. `R/...` paths refer to `css_drug_analysis`. The conversation that produced this design is in
`docs/conversations/2026-10-22-design-conversation.md`.

## Why

Checkbook's data catalog has two tables: `master_warehouse_run`, which records each job run, and `data_file`, which
records each file a run produced. Today two separate codebases write to it:

- **This NPD loader (Python).** It writes to Postgres `css_catalog_local` through `CssCatalogPg` in
  `src/npd_loader/catalog.py`.
- **css_drug_analysis (R).** It writes to SQL Server `HIE_WAREHOUSE_META` on `cssnpi` through scripts that each
  project copies and loads with `source()` (`R/css_apis/catalog/*.r`). Those scripts use RODBC, build SQL by
  pasting strings together, and read back new ids with `@@IDENTITY`, which is not safe when jobs run concurrently.

New projects are on the way, starting with downloading CCIIO PUF files. They need the same four things:

1. **Acquire and record.** Start a run, put files in a predictable place, and record each file with its source,
   version, path, size, hash and parent file.
2. **Find.** Look up files and runs.
3. **Trace.** Downstream tables and data frames carry the catalog file id in a column named **`file_id`** (owner's
   decision), so any result can be traced back to the exact file it came from. The library's docs, examples and
   helpers use `file_id` for this column. NPD rows already do this with `ndjson_file_id` and `zst_file_id`. Those
   names stay as they are, because the migration must not change NPD's tables, and a row there carries two ids.
4. **Compare runs by configuration** (owner's goal, added 2026-10-25). Find earlier runs whose configuration is
   identical to a new one ("has this exact download already run?"). Show how two similar configurations differ, so
   a person can decide whether a run is a new instance of the same job.

Three Checkbook users (the owner and two colleagues) will use this from both Python and R. It needs to be a real
library, with one tested implementation per language, instead of code copied from project to project.

## Goals

- A single GitHub repo, `css-catalog-client`, that holds a Python package and an R package.
- Both packages support two backends: Postgres (`css_catalog_local`) and SQL Server (`HIE_WAREHOUSE_META`). The
  caller picks one in configuration. A third backend, the HIE Warehouse ASMX web service (the "Warehouse API"), is
  stubbed but not implemented (see below).
- The two packages behave the same. A shared set of contract tests proves it, run against real Postgres and SQL
  Server instances wherever those are available.
- Adding the planned cloud catalog later (a REST API) means adding a backend, not changing calling code.
- Move this NPD loader onto the Python package, with its behavior unchanged.
- Write candidate R code that moves css_drug_analysis onto the R package. The code goes in the library repo and is
  **not written into the css_drug_analysis folder**.
- Installing from GitHub works for both languages, and each project pins a release tag.
- Both packages can find runs with an identical configuration and compare two configurations setting by setting,
  with the same results in both languages and on every backend (see "Config matching").

## Non-goals

- **Standardizing conventions.** The library gives no meaning to `run_type_root_dir`, `file_set`, `file_type`,
  `source_version_num` or `run_type`. Other people use these fields in different ways, and those uses have not been
  reconciled yet. Each project passes its own values. Standardizing them is future work.
- **Lookups based on conventions.** For example, "give me the Rate PUF for 2024–2026." That needs the conventions
  above and is on the roadmap. The design should leave room for it but not build it.
- The cloud REST backend itself (not yet available).
- **Implementing the Warehouse API backend.** Only its stub is built now.
- Prefect integration code. The design only has to keep Prefect easy to add later (see below).
- Changing either database schema.
- Building the CCIIO PUF project. It is the motivating use case, but it is a separate project.
- **Deciding that similar configurations are "the same".** The library matches only configurations that are
  identical (as defined under "Config matching") and otherwise shows the differences. Judging near-matches is left to
  a person.

## Anti-goals (failure even if it works)

- The Python and R packages drift apart and record runs or files differently.
- The library builds in one project's conventions, such as NPD's `release_date` in `xml_config`, and every other user
  has to work around them.
- The NPD loader behaves differently after the migration: other run rows, other file rows, other file names, or a
  status report that changes.
- R users need a working Python install (so no reticulate).
- The R package still needs RODBC, directly or indirectly. The owner wants catalog access in R on standard DBI only.

## Constraints

- The schemas are fixed. Postgres DDL is in `tests/sql/css_catalog_schema.sql`. The integer key on
  `master_warehouse_run` is `id`, while `run_id` is a uuid. `data_file.run_id` references `master_warehouse_run.id`.
  The SQL Server tables have the same columns, but with uppercase names (DDL in the appendix). Differences
  that matter:
  - SQL Server `DATA_FILE` has **no foreign keys**, either `RUN_ID` → run or `PARENT_FILE` → file. Postgres has
    both. The library does not rely on the database to enforce these links.
  - `XML_CONFIG` and `XML_OUTPUT` are `xml` columns on both backends. Pass the text as a parameter. SQL Server
    converts nvarchar to xml implicitly, and Postgres needs an `::xml` cast.
  - Defaults on SQL Server: `RUN_ID` = `newid()`, `DATE_STARTED` = `getdate()`, `USER` = `suser_sname()`. On
    Postgres: `gen_random_uuid()`, `LOCALTIMESTAMP`, `SESSION_USER`.
  - No triggers on either table, so `OUTPUT INSERTED.ID` is safe.
- Column limits: `result` is varchar(2000) and `exceptions` is varchar(8000). Truncate as `catalog.py` does now.
- Every catalog write commits on its own (its own short connection or autocommit), so a failed data load never
  rolls back catalog rows. This is how `CssCatalogPg` works today, and the R code also behaves this way.
- Windows is the main platform. SQL Server is reached through ODBC with Windows trusted connections. Postgres uses a
  username and password.
- Python ≥ 3.12, matching this repo. R: whatever recent version the team uses (the builder confirms it).

## What the library does

The same operations exist in both languages, with idiomatic names in each.

**Runs**
- `start_run(project, run_type, run_class, description, config, parent_run_id=None)` → Run (int `id`, uuid
  `run_id`, `date_started`). `config` is a mapping, serialized to `<WAREHOUSE_RUN_CONFIG>` XML.
  *Design call:* the contract defines **nested** serialization. A nested mapping becomes nested elements, a scalar
  becomes element text, and `None`/NULL becomes empty text. Python's output for a flat mapping is exactly what
  `runxml.build_config_xml` produces today, so NPD's output does not change. R's `list.to.xml` extras are kept
  in the R package only, to preserve today's Part D `xml_config`: multi-element vectors become attributes,
  data.frames become `<row>` children, and functions are skipped. The scenarios test the shared nested subset.
  *Amended 2026-10-25 (owner's decision):* the two languages must produce **equivalent** XML (see "Config
  matching"), not byte-identical text. R builds and parses XML with the `xml2` package instead of hand-written
  serialization, and replaces css_drug_analysis's `list.to.xml` (and its dependency on the old `XML` package). The
  shared `spec/xml` fixtures are compared in canonical form in both languages.
- `finish_run(run, status, result=None, output_xml=None)`. Status labels come from configuration, with defaults
  `Success`/`Failed`.
- `get_run(id)` and `find_runs(project=, run_type=, run_class=, status=)`. These return runs, including
  `xml_config` text. Choosing the "newest release" or similar is the caller's job (see the NPD migration below).

**Config matching** (added 2026-10-25)
- **Identical** means the same settings with the same values, ignoring order and formatting:
  - the order in which settings appear in the XML (`<run_type>` before or after `<file_set>`) does not matter;
  - formatting does not matter: `<a/>`, `<a />` and `<a></a>` are the same, as are attribute order and whitespace
    between elements;
  - values are compared exactly as text: `"Medicare Part D PUF"` and `"Medicare Part D PUF quarterly"` differ, and
    so do Python `True` and R `TRUE` (the contract's portable-scalar rule applies);
  - repeated elements with the same name (R vectors written as repeated elements, data.frame `<row>`s) keep their
    order relative to each other.
- `canonical_config_xml(config_or_xml)` → the one canonical text form of a configuration, defined exactly in
  `spec/contract.md` so both languages produce the same bytes.
- `config_fingerprint(config_or_xml)` → lowercase sha256 hex of the canonical form. Callers may store it or group by
  it. Two configurations are identical exactly when their fingerprints are equal.
- `find_runs_with_config(config, project=, run_type=, run_class=, status=)` → runs whose stored `xml_config` is
  identical to `config`, ordered by id. The filters narrow the candidates in the database; the comparison happens in
  the client (no schema change, no stored fingerprint column).
- `compare_configs(a, b)` → one row per setting path (for example `source_data/existing_data_files_sql`), with its
  value in each configuration and a status `same`, `different`, `only_a` or `only_b`. `a` and `b` may each be a Run,
  a mapping or XML text. This supports manual review of near-matches.
- Shared scenarios cover the same configuration written in different orders and formats, and matching across
  backends (a run stored on SQL Server, whose XML the server re-serializes, still matches).

**Files**
- `add_file(run, **fields)` → int id. `update_file(id, **fields)`. Field names are the `data_file` columns. Unknown
  fields are rejected (as `clean_fields` does now).
- `get_file(id)`, `files_of_run(run_id)`, and `find_files(file_set=, file_type=, source_version_num=, run_id=)`.
  These are exact-match filters on the strings the caller passes.
- `lineage(file_id)`. Follows `parent_file` up to the root and returns the chain, which is the traceability lookup.
- `parent_run_id` is set on child runs, so a run tree can be walked too.

**Optional helpers.** These are conveniences that projects can use or skip. They are not conventions the library
enforces.
- `file_stats(path, hash="sha256"|"md5")` returns size, hash and created/modified times. NPD uses sha256 and the R
  code uses md5. The algorithm is the caller's choice. The library does not record which algorithm was used, beyond
  what the hash length implies.
- A run-directory and file-name layout matching what both codebases already do: files named `file_<id>_<name>`,
  and R's run folders named `run_<id>_[<label>_]<timestamp>`. Provide "register then rename" as a single helper
  (insert the row, rename the file to include its id, update `file_name`), because both codebases do this by hand.

**Backends.** One interface per language with two implementations, Postgres and SQL Server. There is also a stub
for the Warehouse API (next section), and the cloud REST API follows later. The database backends:
Table names are configurable (today `public.master_warehouse_run` / `public.data_file` on Postgres, and
`dbo.MASTER_WAREHOUSE_RUN` / `dbo.DATA_FILE` on SQL Server). New ids come from `RETURNING id` on Postgres and
`OUTPUT INSERTED.id` on SQL Server, never `@@IDENTITY`. All values are passed as query parameters, including the XML.

**Python:** a `Catalog` protocol (generalizing the one in `catalog.py`), with `PgCatalog` on psycopg 3 and
`MssqlCatalog` on pyodbc. Also an in-memory fake that ships with the package for unit tests in calling projects. It
replaces `tests/fakes.py` here.

**R:** one exported constructor per backend, built on DBI: RPostgres for Postgres and odbc for SQL Server.
**All catalog database access goes through standard DBI** (`dbConnect`, `dbGetQuery`/`dbSendQuery` with
parameters, `dbExecute`, `dbWithTransaction` where needed). RODBC and RODBCext are not dependencies, not even
optional ones. A test checks that neither appears in `DESCRIPTION` or the package code. Callers can also pass in a
DBI connection they already have, in place of connection settings. Functions take the catalog object as their first argument. Every run or file
result is a tibble with the same columns and types: a single record (`start_run`, `get_run`, `get_file`,
`register_and_rename`) is a 1-row tibble, so results stack with `dplyr::bind_rows` the way today's
`initialize_warehouse_run` / `log_file_in_warehouse` data frames do (owner's decision, 2026-10-25).

## Warehouse API backend (HIE Warehouse ASMX service): stub only

**It exists.** There is a third way to reach the catalog: the HIE Warehouse ASMX web service, a local API over the
same catalog. The owner's two minimal clients for it are saved in `docs/reference/warehouse-asmx/`. Briefly, from
those clients: calls are form POSTs to `<WAREHOUSE_URL>/<Method>` (default
`http://localhost/Warehouse/Service.asmx`), each one carrying a GUID token (`WAREHOUSE_TOKEN`). Logins use Windows
NTLM. Responses are JSON rows with an `ERROR` field. The known methods are `Create`, `Check` (get-or-create),
`RegisterFile` and `ListAll`. `Create` makes the run folder on the server and returns it as `DATASTOREPATH`.

**Build now: only the stub (owner's decision).**
- Python: a `WarehouseApiCatalog` class that satisfies the `Catalog` protocol. R: a matching constructor. Both
  accept `url`, `token`, `user` and `password`, defaulting to the `WAREHOUSE_*` environment variables. Every
  operation raises `NotImplementedError` (Python) or stops with a classed condition (R). The message says the
  Warehouse API backend is not implemented yet.
- The config/backend selector recognizes the name `warehouse_api` and builds the stub, so the option is visible
  and can be selected.
- One test per language checks that the stub can be constructed and that each operation raises the
  not-implemented error.
- No HTTP dependencies are added. Nothing in the interface is shaped around this backend yet.

**When it's implemented later, it will need:**
- the service's full method list (`Service.asmx?WSDL`);
- what `Path`, `Group` and `SVN` map to;
- whether `RegisterFile` computes size and hash, renames the file, and returns an id;
- which database the service writes to.

It will also need two things the interface doesn't have yet: a way to tell callers which operations a backend
supports, since the API seems to have fewer, and an optional server-made run folder on `Run`.

## Keeping the two languages in step

- `spec/xml/` fixtures are compared in canonical form (see "Config matching"), not byte for byte.
- `spec/` in the repo holds the operation contract in prose, plus the DDL for both backends, used to build test
  databases.
- `spec/scenarios/` holds language-neutral test scenarios (YAML or JSON): a sequence of operations and the expected
  rows. pytest and testthat each run every scenario against every backend. NPD's `tests/catalog_contract.py` is the
  starting point.
- **CI: none for now (owner's decision, 2026-10-23).** GitHub CI is off. The repo has a stub Azure DevOps
  pipeline (`azure-pipelines.yml`) that runs only when triggered by hand. It sets out the intended jobs
  (Postgres and SQL Server service containers, then both suites) for whoever wires it up later. Until then,
  the suites run locally: Postgres against the owner's portable server, and SQL Server only once a test
  server or LocalDB is available. The SQL Server backend therefore stays **unverified** until then. Test
  containers can't use Windows trusted connections, so the SQL Server backend also supports SQL authentication
  (*design call*). Unit tests that need no database run in each language.

## Repo, install and maintenance

- **Repo:** GitHub, `css-catalog-client`, under the owner's individual account for now. It starts private, and access for
  colleagues gets sorted out later. The install docs must therefore cover token setup: for pip, a GitHub PAT
  through Git Credential Manager; for R, `GITHUB_PAT` in `.Renviron`. The repo is laid out as `python/` (package `css_catalog`), `r/` (package `csscatalog`). The package names stay short and
  don't match the repo name (owner's decision). The Python distribution is `css-catalog` (`import css_catalog`).
  R names can't contain `_` or `-`, which is why R's is `csscatalog`. The repo also has
  `spec/`, `examples/`, `docs/` and `tools/`.
- **Python install:** `pip install "css-catalog @ git+https://github.com/<owner>/css-catalog-client@vX.Y.Z#subdirectory=python"`,
  pinned in each project's `pyproject.toml`.
- **R install:** `remotes::install_github("<owner>/css-catalog-client", subdir = "r", ref = "vX.Y.Z")`, locked with renv.
- **Releases:** one version number for both packages, tagged `vX.Y.Z`, with a CHANGELOG and semantic versioning. A
  release is cut only when both languages' suites pass locally. Release notes must say whether the SQL Server
  backend was tested for that release. Projects upgrade when they choose to.
- **Ownership:** one maintainer, the owner. The two colleagues contribute through PRs.

## Prefect (later; the design must not block it)

A Prefect flow starts a parent catalog run, and its tasks start child runs with `parent_run_id`. R steps run as
`Rscript` tasks that receive the parent run id and backend settings through environment variables or arguments.
The packages should read catalog connection settings from environment variables as one supported option, so a
Prefect worker can configure them without project-specific files.

## NPD loader migration

- Replace `src/npd_loader/catalog.py`'s `CssCatalogPg` with `css_catalog.PgCatalog`. Keep NPD-only logic in NPD:
  `release_date` parsing from `xml_config` (`runxml.parse_release`), `newest_run` / `releases_of`, the
  `get_data_files(release, file_type)` meaning of `source_version_num`, and the `[catalog]` config fields that name
  NPD's run classes and file types. These become a thin NPD module over the library.
- Keep `[catalog]` in `config.example.toml` working as it does now, and add a `backend` value for SQL Server.
- *Design call:* NPD keeps its own `Catalog` protocol (`start_run(run_class, description, config_xml)`,
  `last_successful_run`, `successful_releases`, `get_data_files(release, ...)`) as a thin adapter over the
  library. That way `stages.py` and the stage tests don't change. `tests/fakes.py` becomes the library fake plus
  NPD's helper methods (`add_successful_run`, ids starting at 62000). Tests may be edited only where they name
  `CssCatalogPg` or import paths. No assertion changes.
- Done when: every existing test passes (`tests/test_catalog_pg.py`, the contract tests, and `test_e2e.py`), and the
  run and file rows written for a release match the pre-migration rows field for field (except ids and timestamps).

## css_drug_analysis candidate code

Deliver these as text files in `examples/css_drug_analysis/` in the library repo, laid out to mirror the project
(`R/data_acquisition/...`, `R/config/...`, `R/data_assembly/...`). **Nothing is written to the css_drug_analysis
folder.**

- A rewrite of `catalog_medicare_part_d_quarterly_price_data.r` that uses `csscatalog`. It keeps the same SQL Server
  catalog, the same `file_set`/`file_type`/`source_version_*` values, and the same `run_<id>_<timestamp>/data/file_<id>_<name>`
  layout, and drops the `source()` lines for `R/css_apis/catalog/*`. It does not use RODBC anywhere. Its own
  queries (the `cms_index` dataset URL lookup and the existing-files check) also go through DBI/odbc.
- Run rows gain fields that are empty today. `PROJECT = 'MEDICARE_DATA'` (owner's decision). `RUN_CLASS = 'DOWNLOAD'`
  (*design call*, matching NPD's download class). Each run is finished with `Success`, or with `Failed` plus the
  error in `RESULT` if that release's download or extraction errors (*design call*). A failure in one release
  should not stop the loop over the other releases.
- An updated config function with the backend settings.
- `read_mpc_drug_price_data.r` keeps finding files by globbing, since convention-based lookup is out of scope. As a
  first step toward traceability, it parses `file_<id>_` from each path and adds a `file_id` column to the data
  it reads.
- A short README listing what to delete from css_drug_analysis (`R/css_apis/catalog/`) and how to install the package.

## Known differences between the codebases (caller-supplied, not reconciled)

The library takes no position on any of these. Each one is a value the caller passes or an option:
- `file_rel_path` includes the file name in NPD but is only the directory in R.
- `run_type_root_dir` is a storage URI in NPD but the folder basename in R.
- `date_started` is set by the client in NPD but by the database default in R. `start_run` accepts an optional
  timestamp and otherwise leaves it to the database (*design call*).
- R can resume an existing run by id. `get_run(id)` covers this.

## Left to the builder

- Function and argument names in each language, within the shape above.
- The format and runner for the shared scenarios.
- How connection settings are layered (arguments, environment variables, config file), as long as environment
  variables are supported.

## Appendix: SQL Server DDL (HIE_WAREHOUSE_META, from SSMS, 2026-10-23)

Copy this into `spec/ddl/mssql.sql` for test servers and containers. The Postgres DDL is `tests/sql/css_catalog_schema.sql` in
the NPD repo.

```sql
CREATE TABLE [dbo].[MASTER_WAREHOUSE_RUN](
	[ID] [int] IDENTITY(1,1) NOT NULL,
	[PARENT_RUN_ID] [int] NULL,
	[RUN_TYPE] [nvarchar](200) NOT NULL,
	[RUN_ID] [uniqueidentifier] NOT NULL,
	[COMPLETION_STATUS] [nvarchar](2000) NULL,
	[DATE_COMPLETED] [datetime] NULL,
	[DATE_STARTED] [datetime] NULL,
	[NOTES] [nvarchar](4000) NULL,
	[RUN_DESCRIPTION] [nvarchar](200) NULL,
	[RESULT] [nvarchar](2000) NULL,
	[USER] [nvarchar](50) NOT NULL,
	[XML_CONFIG] [xml] NULL,
	[XML_OUTPUT] [xml] NULL,
	[PROJECT] [varchar](1000) NULL,
	[RUN_CLASS] [varchar](100) NULL,
 CONSTRAINT [PK__MST_RUN__3214EC27D1F7F566] PRIMARY KEY CLUSTERED ([ID] ASC)
);
ALTER TABLE [dbo].[MASTER_WAREHOUSE_RUN] ADD CONSTRAINT [DF_MASTER_WAREHOUSE_RUN_RUN_ID] DEFAULT (newid()) FOR [RUN_ID];
ALTER TABLE [dbo].[MASTER_WAREHOUSE_RUN] ADD CONSTRAINT [DF_MASTER_WAREHOUSE_RUN_DATE_STARTED] DEFAULT (getdate()) FOR [DATE_STARTED];
ALTER TABLE [dbo].[MASTER_WAREHOUSE_RUN] ADD CONSTRAINT [DF_MASTER_WAREHOUSE_RUN_USER] DEFAULT (suser_sname()) FOR [USER];
ALTER TABLE [dbo].[MASTER_WAREHOUSE_RUN] WITH CHECK ADD CONSTRAINT [FK__MASTER_WA__PAREN__6225902D]
    FOREIGN KEY([PARENT_RUN_ID]) REFERENCES [dbo].[MASTER_WAREHOUSE_RUN] ([ID]);

CREATE TABLE [dbo].[DATA_FILE](
	[ID] [int] IDENTITY(1,1) NOT NULL,
	[RUN_ID] [int] NOT NULL,
	[FILE_SET] [varchar](500) NULL,
	[FILE_TYPE] [varchar](500) NULL,
	[SOURCE_URI] [varchar](2000) NULL,
	[SOURCE_VERSION_NAME] [varchar](500) NULL,
	[SOURCE_VERSION_NUM] [varchar](500) NULL,
	[FILE_NAME] [varchar](2000) NULL,
	[FILE_REL_PATH] [varchar](2000) NULL,
	[RUN_TYPE_ROOT_DIR] [varchar](2000) NULL,
	[PARENT_FILE] [int] NULL,
	[FILE_SIZE] [bigint] NULL,
	[FILE_HASH] [varchar](1000) NULL,
	[DATE_LOADED] [datetime] NULL,
	[DATE_MODIFIED] [datetime] NULL,
	[DATE_CREATED] [datetime] NULL,
	[EXCEPTIONS] [varchar](8000) NULL,
 CONSTRAINT [PK_CCIIO_PUF_FILE] PRIMARY KEY CLUSTERED ([ID] ASC)
);
```
