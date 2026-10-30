"""Catalog rows written by the first-release flow, compared against a snapshot of the rows the loader wrote
before the move to css_catalog. Surrogate ids, uuids and timestamps are normalized away."""
import json
import os
import re
from pathlib import Path

import psycopg
import pytest

from npd_loader.cli import main
from helpers import config_data, to_toml
from test_e2e import publish

SNAPSHOT = Path(__file__).parent / "data" / "catalog_rows_snapshot.json"

RUN_COLUMNS = ["id", "parent_run_id", "run_type", "completion_status", "notes", "run_description", "result",
               "user", "xml_config", "xml_output", "project", "run_class"]
FILE_COLUMNS = ["id", "run_id", "file_set", "file_type", "source_uri", "source_version_name",
                "source_version_num", "file_name", "file_rel_path", "run_type_root_dir", "parent_file",
                "file_size", "file_hash", "exceptions"]


def normalized_rows(catalog_conninfo: str) -> dict:
    with psycopg.connect(catalog_conninfo) as conn:
        cols = ", ".join(f'"{c}"' if c == "user" else (f"{c}::text AS {c}" if c.startswith("xml_") else c)
                         for c in RUN_COLUMNS)
        runs = [dict(zip(RUN_COLUMNS, r)) for r in
                conn.execute(f"SELECT {cols} FROM master_warehouse_run ORDER BY id").fetchall()]
        files = [dict(zip(FILE_COLUMNS, r)) for r in
                 conn.execute(f"SELECT {', '.join(FILE_COLUMNS)} FROM data_file ORDER BY id").fetchall()]
    run_pos = {r["id"]: i for i, r in enumerate(runs)}
    file_pos = {f["id"]: i for i, f in enumerate(files)}

    def scrub(text):
        """Per-test noise: the fake CMS port and the pytest temp directory."""
        if text is None:
            return None
        text = re.sub(r"127\.0\.0\.1:\d+", "127.0.0.1:<port>", text)
        return re.sub(r"file:///\S*?/data(?=[/<\"]|$)", "file:///<data>", text)

    def remap_path(text, pos=None):
        """run_<id>_<timestamp>/file_<id>_name -> run_<pos>_<ts>/file_<pos>_name."""
        if text is None:
            return None
        text = re.sub(r"run_(\d+)_\d{4}-\d\d-\d\d-\d{6}", lambda m: f"run_{run_pos[int(m[1])]}_<ts>", text)
        if pos is not None:
            text = re.sub(r"file_\d+_", f"file_{pos}_", text)
        return text

    def remap_xml(text):
        if text is None:
            return None
        text = scrub(text)
        text = re.sub(r'file_id="(\d+)"', lambda m: f'file_id="{file_pos[int(m[1])]}"', text)
        text = re.sub(r"<download_run_id>(\d+)</download_run_id>",
                      lambda m: f"<download_run_id>{run_pos[int(m[1])]}</download_run_id>", text)
        text = re.sub(r"<extract_run_ids>([\d,]*)</extract_run_ids>",
                      lambda m: "<extract_run_ids>" + ",".join(str(run_pos[int(i)]) for i in m[1].split(",") if i)
                      + "</extract_run_ids>", text)
        return re.sub(r'seconds="[^"]*"', 'seconds="<s>"', text)

    out_runs = []
    for r in runs:
        row = dict(r)
        row["id"] = f"#{run_pos[r['id']]}"
        row["parent_run_id"] = None if r["parent_run_id"] is None else f"#{run_pos[r['parent_run_id']]}"
        row["xml_config"] = remap_xml(r["xml_config"])
        row["xml_output"] = remap_xml(r["xml_output"])
        out_runs.append(row)
    out_files = []
    for pos, f in enumerate(files):
        row = {k: scrub(v) if isinstance(v, str) else v for k, v in f.items()}
        del row["id"]
        row["run_id"] = f"#{run_pos[f['run_id']]}"
        row["parent_file"] = None if f["parent_file"] is None else file_pos[f["parent_file"]]
        row["file_name"] = remap_path(row["file_name"], pos)
        row["file_rel_path"] = remap_path(row["file_rel_path"], pos)
        out_files.append(row)
    return {"runs": out_runs, "files": out_files}


def test_catalog_rows_match_snapshot(tmp_path, cms, npd_db, catalog_db):
    data = config_data(tmp_path / "data", cms.manifest_url, npd_conninfo=npd_db, catalog_conninfo=catalog_db)
    config_path = tmp_path / "config.toml"
    config_path.write_text(to_toml(data))
    assert main(["--config", str(config_path), "init-db"]) == 0
    publish(cms, 0)
    assert main(["--config", str(config_path), "run"]) == 0

    rows = normalized_rows(catalog_db)
    if not SNAPSHOT.exists():
        if os.environ.get("NPD_WRITE_CATALOG_SNAPSHOT") == "1":
            SNAPSHOT.parent.mkdir(exist_ok=True)
            SNAPSHOT.write_text(json.dumps(rows, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            pytest.skip("snapshot written")
        pytest.fail(f"{SNAPSHOT} is missing; set NPD_WRITE_CATALOG_SNAPSHOT=1 to write it")
    assert rows == json.loads(SNAPSHOT.read_text(encoding="utf-8"))
