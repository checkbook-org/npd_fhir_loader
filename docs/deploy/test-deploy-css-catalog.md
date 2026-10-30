# Test deployment: css-catalog migration on 192.10.0.7

This runbook installs the `css-catalog-client-migration` branch on 192.10.0.7 **next to** the production install,
and runs a full load into separate test databases on 192.10.0.6. Production is not touched: its install, config,
timer, data folder and databases (`npd`, `css_catalog_local`) stay as they are. After the test passes, the
section *Upgrading production* covers the real rollout.

| | Production (unchanged) | Test |
|---|---|---|
| Install | `/home/cbadmin/national_provider_dir/npd-loader` | `/home/cbadmin/national_provider_dir/npd-loader-test` |
| Config | `/etc/npd-loader/config.toml` | `/etc/npd-loader/config-test.toml` |
| Data folder | `/data/npd` | `/data/npd_test` |
| NPD database (192.10.0.6) | `npd` | `npd_test` |
| Catalog database (192.10.0.6) | `css_catalog_local` | `css_catalog_test` |

The two can run at the same time without affecting each other: the loader's locks are per NPD database, and every
path and database differs. They do share the 192.10.0.6 server's CPU and disk, so start the test after the 06:00
production run has finished.

## 0. Before you start

On the workstation:

1. The tag `v0.1.0` is pushed to `Consumers-Checkbook/css-catalog-client`.
2. The branch's `pyproject.toml` pins
   `css-catalog @ git+https://github.com/Consumers-Checkbook/css-catalog-client@v0.1.0#subdirectory=python`
   (plan 3, Task 4).
3. The branch `css-catalog-client-migration` is committed and pushed to `checkbook-org/npd_fhir_loader`. Don't
   merge it yet.

On the servers:

4. 192.10.0.7: cbadmin can read css-catalog-client. See `github-token-css-catalog.md`; its step 3
   must pass.
5. 192.10.0.6: about **90 GB** free for `npd_test`. One release is about 75 GB, plus working space.
6. 192.10.0.7: about **45 GB** free on the filesystem holding `/data/npd_test`. That's 2.4 GB of `.zst` plus
   35 GB of `.ndjson` per release.
7. You can connect to 192.10.0.6 with `psql` as a role that can create databases.

Installs run as `cbadmin`, which holds the GitHub token. Placeholders used below: `<pg_admin>` is
the Postgres role that creates databases; `<npd_user>` and `<catalog_user>` are the `user` values in
`[npd_db]` and `[catalog]` of `/etc/npd-loader/config.toml`.

## 1. Install the branch beside production (192.10.0.7)

```bash
sudo mkdir -p /data/npd_test
sudo chown npd: /data/npd_test

# as cbadmin
mkdir -p /home/cbadmin/national_provider_dir/npd-loader-test
git clone -b css-catalog-client-migration https://github.com/checkbook-org/npd_fhir_loader.git /home/cbadmin/national_provider_dir/npd-loader-test/src
python3.12 -m venv /home/cbadmin/national_provider_dir/npd-loader-test/.venv
/home/cbadmin/national_provider_dir/npd-loader-test/.venv/bin/pip install /home/cbadmin/national_provider_dir/npd-loader-test/src
/home/cbadmin/national_provider_dir/npd-loader-test/.venv/bin/pip show css-catalog | head -2
```

Expect `Name: css-catalog` and `Version: 0.1.0`. If pip asks for a GitHub username or fails while cloning
css-catalog-client, the token isn't set up for this account. Go back to `github-token-css-catalog.md`.

The `npd` service account runs the loader, so it needs to reach the venv inside cbadmin's home folder. Production
already needs this, so it is probably in place. Check:

```bash
sudo -u npd /home/cbadmin/national_provider_dir/npd-loader-test/.venv/bin/npd-loader --help
```

If that fails with `Permission denied`, give `npd` execute-only access to the two parent folders (no right to list
them): `sudo setfacl -m u:npd:x /home/cbadmin /home/cbadmin/national_provider_dir`.

## 2. Create the test databases (192.10.0.6)

```bash
psql -h 192.10.0.6 -U <pg_admin> -d postgres <<'SQL'
CREATE DATABASE npd_test OWNER <npd_user>;
CREATE DATABASE css_catalog_test;
SQL

# the real css_catalog_local DDL (pg_dump of production, owners and grants removed)
psql -h 192.10.0.6 -U <pg_admin> -d css_catalog_test -v ON_ERROR_STOP=1 \
     -f /home/cbadmin/national_provider_dir/npd-loader-test/src/tests/sql/css_catalog_schema.sql

psql -h 192.10.0.6 -U <pg_admin> -d css_catalog_test <<'SQL'
GRANT USAGE ON SCHEMA public TO <catalog_user>;
GRANT SELECT, INSERT, UPDATE ON public.master_warehouse_run, public.data_file TO <catalog_user>;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO <catalog_user>;
SQL
```

If `<npd_user>` can't own a database at your site, create `npd_test` with another owner and run
`GRANT CREATE ON DATABASE npd_test TO <npd_user>;` instead.

## 3. Test config (192.10.0.7)

Copy the production config and change exactly three values:

```bash
sudo cp /etc/npd-loader/config.toml /etc/npd-loader/config-test.toml
sudo chown npd: /etc/npd-loader/config-test.toml
sudo chmod 600 /etc/npd-loader/config-test.toml
sudoedit /etc/npd-loader/config-test.toml
```

- `[storage] root = "/data/npd_test"`
- `[npd_db] dbname = "npd_test"`
- `[catalog] dbname = "css_catalog_test"`

Check that nothing else differs. Do this before running anything, because a wrong `dbname` here would write into
production:

```bash
sudo diff /etc/npd-loader/config.toml /etc/npd-loader/config-test.toml
```

The output should show only those three lines changed.

## 4. Initialize and check

```bash
T="sudo -u npd /home/cbadmin/national_provider_dir/npd-loader-test/.venv/bin/npd-loader --config /etc/npd-loader/config-test.toml"
$T init-db
$T status
```

`init-db` exits 0. `status` prints only the header line, since there are no releases yet.

## 5. Full run

Run it as a transient systemd unit, so it survives logging out and its log goes to the journal:

```bash
sudo systemd-run --unit=npd-loader-test --uid=npd \
     /home/cbadmin/national_provider_dir/npd-loader-test/.venv/bin/npd-loader --config /etc/npd-loader/config-test.toml run
journalctl -u npd-loader-test -f        # Ctrl-C stops following, not the run
```

The sequence:
- download: a few minutes
- extract: a few minutes
- raw load
- transforms (`010_practitioner.sql` … `090_identifier.sql`)
- `published release YYYY-MM-DD (27 tables)`
- `run: import: success`

Expect several hours in total; on a workstation it took about 4 h. The run is done when the journal shows
`npd-loader-test.service: Deactivated successfully`. On a failure it shows `Main process exited, code=exited,
status=1/FAILURE`, plus the error a few lines above.

## 6. Verify

```bash
$T status        # one release, its download/extract/import run ids, published = yes
```

```bash
psql -h 192.10.0.6 -U <catalog_user> -d css_catalog_test <<'SQL'
SELECT id, run_class, completion_status, date_started, date_completed, left(result, 200) AS result
  FROM master_warehouse_run ORDER BY id;
SELECT run_id, file_type, count(*) AS files, count(file_hash) AS hashed, count(date_loaded) AS loaded,
       count(exceptions) AS exceptions
  FROM data_file GROUP BY 1, 2 ORDER BY 1, 2;
SQL
```

Expected:
- `master_warehouse_run` has 3 rows: DOWNLOAD, EXTRACT and IMPORT, all `Success`, with an empty `result`.
- `data_file` has 17 rows:
  - the DOWNLOAD run: 1 `manifest` and 8 `ndjson.zst`
  - the EXTRACT run: 8 `ndjson`
  - all 17 are hashed, all 8 `ndjson` are loaded, and there are 0 exceptions.

```bash
psql -h 192.10.0.6 -U <npd_user> -d npd_test <<'SQL'
SELECT * FROM npd.release;
SELECT 'practitioner' AS t, count(*) FROM npd.practitioner
UNION ALL SELECT 'practitioner_role', count(*) FROM npd.practitioner_role
UNION ALL SELECT 'organization', count(*) FROM npd.organization
UNION ALL SELECT 'location', count(*) FROM npd.location;
SQL
```

Expect one release, with about 7.5M practitioners, 11.2M practitioner roles, 2.1M organizations and 2.6M
locations for a late-2026 release.

**Strongest check:** if production has already loaded the **same release**, the two IMPORT runs must report
identical row counts for every table. Run this on both `css_catalog_test` and `css_catalog_local`, and compare
the output:

```sql
SELECT xml_output FROM master_warehouse_run
 WHERE run_class = 'IMPORT' AND completion_status = 'Success'
 ORDER BY id DESC LIMIT 1;
```

Finally, run again. Both stages must be skipped:

```bash
$T run           # "already downloaded", "already imported", exit 0
```

Check that `master_warehouse_run` still has 3 rows.

## 7. Clean up the test

Once you no longer need it:

```bash
psql -h 192.10.0.6 -U <pg_admin> -d postgres -c 'DROP DATABASE npd_test' -c 'DROP DATABASE css_catalog_test'
sudo rm -rf /data/npd_test /home/cbadmin/national_provider_dir/npd-loader-test /etc/npd-loader/config-test.toml
```

## Upgrading production (after the test passes)

1. Merge the PR into `main`.
2. On 192.10.0.7, check nothing is running (`systemctl status npd-loader.service`), then pause the schedule:
   `sudo systemctl stop npd-loader.timer`.
3. Back up the catalog rows:
   `pg_dump -h 192.10.0.6 -U <pg_admin> -d css_catalog_local -t public.master_warehouse_run -t public.data_file -Fc -f ~/css_catalog_local-before-css-catalog.dump`.
4. As cbadmin:
   ```bash
   git -C /home/cbadmin/national_provider_dir/npd-loader/src pull
   /home/cbadmin/national_provider_dir/npd-loader/.venv/bin/pip install --force-reinstall /home/cbadmin/national_provider_dir/npd-loader/src
   /home/cbadmin/national_provider_dir/npd-loader/.venv/bin/pip show css-catalog | head -2      # Version: 0.1.0
   ```
   `--force-reinstall` is needed because npd-loader's own version number did not change.
5. `sudo -u npd /home/cbadmin/national_provider_dir/npd-loader/.venv/bin/npd-loader --config /etc/npd-loader/config.toml init-db`, then `... status`.
   It must list the existing releases with their run ids.
6. Optional: a manual `... run`. It downloads and imports a new release if there is one, and otherwise skips
   both stages.
7. `sudo systemctl start npd-loader.timer`.

**Rollback:** `git -C /home/cbadmin/national_provider_dir/npd-loader/src checkout <previous commit>`, repeat step 4's `pip install
--force-reinstall`, then restart the timer. The new code writes the same rows to the same tables, so the old
code reads them without change.
