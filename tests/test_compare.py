import psycopg
from psycopg import sql

from npd_loader.cli import main
from npd_loader.compare import CountRow, compare_counts, format_comparison
from npd_loader.publish import publish_release
from npd_loader.schema import init_db
from npd_loader.storage import LocalStorage
from npd_loader.transform import run_transforms
from helpers import config_data, to_toml
from pg_helpers import R, load_fixture_raw


def test_compare_counts_marks_differences_and_missing_tables():
    rows = compare_counts({"npd.a": 2, "npd.b": 3}, {"npd.a": 2, "npd.b": 4, "npd.c": 1})
    assert rows == [CountRow("npd.a", 2, 2), CountRow("npd.b", 3, 4), CountRow("npd.c", None, 1)]
    assert [r.matches for r in rows] == [True, False, False]
    text = format_comparison(rows, "npd_test", "npd")
    assert text.splitlines()[0].split() == ["table", "npd_test", "npd", "match"]
    assert text.splitlines()[3].split() == ["npd.c", "-", "1", "NO"]


def publish_fixture(tmp_path, conninfo, name, run_id=7):
    with psycopg.connect(conninfo) as conn:
        raw = load_fixture_raw(conn, LocalStorage(tmp_path / name), run_id=run_id)
        result = run_transforms(conn, "npd_raw", raw.table, "npd", R, run_id)
        publish_release(conn, "npd_raw", raw.table, "npd", result.tables, R, run_id, False, 5, lambda s: None)


def write_config(tmp_path, name, conninfo):
    path = tmp_path / f"{name}.toml"
    path.write_text(to_toml(config_data(tmp_path / name, "https://example.test/manifest.json",
                                        npd_conninfo=conninfo)))
    return str(path)


def test_compare_command(tmp_path, make_db, capsys, caplog):
    this, other = make_db(), make_db()
    for info in (this, other):
        init_db(info, "npd_raw", "npd")
    this_cfg, other_cfg = write_config(tmp_path, "this", this), write_config(tmp_path, "other", other)

    assert main(["--config", this_cfg, "compare", other_cfg]) == 1
    assert "no release is published in both" in caplog.text

    publish_fixture(tmp_path, this, "this")
    publish_fixture(tmp_path, other, "other", run_id=8)
    assert main(["--config", this_cfg, "compare", other_cfg]) == 0
    out = capsys.readouterr().out
    assert f"release {R}" in out and "npd_raw.resource" in out and "npd.practitioner " in out
    assert "NO" not in out and out.strip().endswith("tables match")

    with psycopg.connect(other) as conn:
        partition = conn.execute("SELECT c.relname FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid "
                                 "JOIN pg_class p ON p.oid = i.inhparent WHERE p.relname = 'practitioner'"
                                 ).fetchone()[0]
        conn.execute(sql.SQL("DELETE FROM {} WHERE ctid IN (SELECT ctid FROM {} LIMIT 1)").format(
            sql.Identifier("npd", partition), sql.Identifier("npd", partition)))
    assert main(["--config", this_cfg, "compare", other_cfg, "--release", R.isoformat()]) == 1
    line = next(l for l in capsys.readouterr().out.splitlines() if l.startswith("npd.practitioner "))
    assert line.split()[-1] == "NO"

    assert main(["--config", this_cfg, "compare", other_cfg, "--release", "2020-01-01"]) == 1
    assert "published in neither database" in caplog.text


def test_compare_refuses_the_same_database(tmp_path, caplog):
    info = "host=127.0.0.1 port=5432 dbname=npd user=u password=p"
    a, b = write_config(tmp_path, "a", info), write_config(tmp_path, "b", info)
    assert main(["--config", a, "compare", b]) == 1
    assert "same NPD database" in caplog.text
