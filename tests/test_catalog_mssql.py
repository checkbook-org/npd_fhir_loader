"""Live SQL Server check of NpdCatalog through open_catalog. Skips unless CSS_TEST_MSSQL_ODBC is set.

Creates and drops its own temporary schema; never point CSS_TEST_MSSQL_ODBC at HIE_WAREHOUSE_META.
"""
import os
import re
import uuid
from pathlib import Path

import pytest

from npd_loader.catalog import SUCCESS, open_catalog
from npd_loader.config import parse_config
from npd_loader.runxml import build_config_xml
from helpers import config_data

DDL = Path(__import__("css_catalog").__file__).resolve().parents[3] / "spec" / "ddl" / "mssql.sql"
RELEASE_XML = build_config_xml({"release_date": "2026-09-29"})


@pytest.fixture
def mssql_schema():
    odbc = os.environ.get("CSS_TEST_MSSQL_ODBC")
    if not odbc:
        pytest.skip("CSS_TEST_MSSQL_ODBC is not set (a throwaway SQL Server; never HIE_WAREHOUSE_META)")
    if not DDL.exists():
        pytest.skip(f"css-catalog-client DDL not found at {DDL}")
    import pyodbc

    schema = f"t_{uuid.uuid4().hex[:12]}"
    ddl = DDL.read_text(encoding="utf-8").replace("{schema}", schema)
    batches = [b.strip() for b in re.split(r"^\s*GO\s*$", ddl, flags=re.MULTILINE | re.IGNORECASE) if b.strip()]
    conn = pyodbc.connect(odbc, autocommit=True)
    try:
        conn.execute(f"CREATE SCHEMA [{schema}]")
        try:
            for batch in batches:
                conn.execute(batch)
            yield odbc, schema
        finally:
            conn.execute(f"DROP TABLE IF EXISTS [{schema}].[DATA_FILE]")
            conn.execute(f"DROP TABLE IF EXISTS [{schema}].[MASTER_WAREHOUSE_RUN]")
            conn.execute(f"DROP SCHEMA [{schema}]")
    finally:
        conn.close()


def test_npd_catalog_mssql_round_trip(tmp_path, mssql_schema):
    odbc, schema = mssql_schema
    data = config_data(tmp_path, "https://example.test/downloads/manifest.json")
    data["catalog"] = {"backend": "mssql", "odbc": odbc, "project": "NPD",
                       "run_type": "National Provider Directory", "file_set": "NPD_FHIR",
                       "run_table": f"{schema}.MASTER_WAREHOUSE_RUN", "file_table": f"{schema}.DATA_FILE"}
    cfg = parse_config(data)
    catalog = open_catalog(cfg.catalog, cfg.catalog.odbc)

    run = catalog.start_run("DOWNLOAD", "NPD FHIR DOWNLOAD 2026-09-29", RELEASE_XML)
    file_id = catalog.add_data_file(run, file_type="ndjson.zst", source_version_num="2026-09-29")
    catalog.finish_run(run, SUCCESS, result="ok")

    last = catalog.last_successful_run("DOWNLOAD")
    assert last is not None and last.id == run.id and last.status == SUCCESS
    assert str(last.release_date) == "2026-09-29"
    assert catalog.successful_releases("DOWNLOAD") == [last.release_date]
    files = catalog.get_data_files(last.release_date, "ndjson.zst", run_id=run.id)
    assert [f.id for f in files] == [file_id]
