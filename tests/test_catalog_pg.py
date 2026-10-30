from dataclasses import replace
from datetime import date

import psycopg
import pytest

from catalog_contract import CatalogContract, cfg
from fakes import FakeCatalog
from npd_loader.catalog import FAILED, SUCCESS, open_catalog
from npd_loader.config import CatalogConfig
from npd_loader.runxml import build_config_xml


def catalog_config() -> CatalogConfig:
    return CatalogConfig(backend="css_catalog_pg", host="unused", port=0, dbname="unused", user=None,
                         password=None, project="NPD", run_type="National Provider Directory", file_set="NPD_FHIR")


class TestNpdCatalogPg(CatalogContract):
    @pytest.fixture
    def catalog(self, catalog_db):
        return open_catalog(catalog_config(), catalog_db)


def test_writes_configured_labels(catalog_db):
    catalog = open_catalog(catalog_config(), catalog_db)
    run = catalog.start_run("DOWNLOAD", "NPD FHIR Download 2026-09-29", cfg(date(2026, 9, 29)))
    fid = catalog.add_data_file(run, file_type="manifest", source_version_num="2026-09-29")
    catalog.finish_run(run, SUCCESS, result=None, output_xml="<WAREHOUSE_RUN_OUTPUT />")
    failed = catalog.start_run("IMPORT", "x", cfg(date(2026, 9, 29)))
    catalog.finish_run(failed, FAILED, result="y" * 9000)
    with psycopg.connect(catalog_db) as conn:
        row = conn.execute("SELECT project, run_type, run_class, run_description, completion_status, "
                           "date_completed IS NOT NULL, parent_run_id FROM master_warehouse_run WHERE id = %s",
                           (run.id,)).fetchone()
        assert row == ("NPD", "National Provider Directory", "DOWNLOAD", "NPD FHIR Download 2026-09-29",
                       "Success", True, None)
        assert conn.execute("SELECT completion_status, length(result) FROM master_warehouse_run WHERE id = %s",
                            (failed.id,)).fetchone() == ("Failed", 2000)
        assert conn.execute("SELECT file_set, source_version_name, run_id FROM data_file WHERE id = %s",
                            (fid,)).fetchone() == ("NPD_FHIR", "NPD release", run.id)


def test_other_projects_runs_are_ignored(catalog_db):
    with psycopg.connect(catalog_db) as conn:
        conn.execute("INSERT INTO master_warehouse_run (project, run_type, run_class, xml_config, completion_status) "
                     "VALUES ('OTHER', 'National Provider Directory', 'DOWNLOAD', %s, 'Success')",
                     (cfg(date(2026, 9, 29)),))
    catalog = open_catalog(catalog_config(), catalog_db)
    assert catalog.last_successful_run("DOWNLOAD") is None


def test_other_run_type_runs_are_ignored(catalog_db):
    with psycopg.connect(catalog_db) as conn:
        conn.execute("INSERT INTO master_warehouse_run (project, run_type, run_class, xml_config, completion_status) "
                     "VALUES ('NPD', 'Some Other Run Type', 'DOWNLOAD', %s, 'Success')",
                     (cfg(date(2026, 9, 29)),))
    catalog = open_catalog(catalog_config(), catalog_db)
    assert catalog.last_successful_run("DOWNLOAD") is None
    assert catalog.successful_releases("DOWNLOAD") == []


def test_open_catalog_ignores_css_catalog_env(monkeypatch):
    monkeypatch.setenv("CSS_CATALOG_BACKEND", "mssql")
    monkeypatch.setenv("CSS_CATALOG_CONNECTION", "env-conn")
    monkeypatch.setenv("CSS_CATALOG_RUN_TABLE", "envs.env_runs")
    monkeypatch.setenv("CSS_CATALOG_FILE_TABLE", "envs.env_files")
    config = replace(catalog_config(), run_table="cfg.runs", file_table="cfg.files")
    inner = open_catalog(config, "host=h dbname=d")._inner
    assert type(inner).__name__ == "PgCatalog"
    assert inner._conninfo == "host=h dbname=d"
    assert "cfg" in str(inner._runs) and "runs" in str(inner._runs) and "env_runs" not in str(inner._runs)
    assert "cfg" in str(inner._files) and "files" in str(inner._files) and "env_files" not in str(inner._files)


def test_custom_status_labels_round_trip(catalog_db):
    config = replace(catalog_config(), status_success="OK", status_failed="Bad")
    catalog = open_catalog(config, catalog_db)
    run = catalog.start_run("DOWNLOAD", "x", cfg(date(2026, 9, 29)))
    catalog.finish_run(run, SUCCESS)
    with psycopg.connect(catalog_db) as conn:
        assert conn.execute("SELECT completion_status FROM master_warehouse_run WHERE id = %s",
                            (run.id,)).fetchone() == ("OK",)
    assert catalog.last_successful_run("DOWNLOAD").id == run.id


def test_run_without_release_is_ignored(catalog_db):
    catalog = open_catalog(catalog_config(), catalog_db)
    catalog.finish_run(catalog.start_run("DOWNLOAD", "x", build_config_xml({"run_type": "x"})), SUCCESS)
    assert catalog.last_successful_run("DOWNLOAD") is None
    assert catalog.successful_releases("DOWNLOAD") == []
    # The xml_config column rejects unparseable XML, so that case runs on the in-memory backend.
    fake = FakeCatalog()
    fake.finish_run(fake.start_run("DOWNLOAD", "x", "<oops"), SUCCESS)
    assert fake.last_successful_run("DOWNLOAD") is None
    assert fake.successful_releases("DOWNLOAD") == []
