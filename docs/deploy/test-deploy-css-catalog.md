# Test deployment: css-catalog migration on 192.10.0.7

This runbook installs the `css-catalog-client-migration` branch on 192.10.0.7 **next to** the production install,
and runs a full load into separate test databases on 192.10.0.6. Production is not touched: its install, config,
timer, data folder and databases (`npd`, `css_catalog_local`) stay as they are. After the test passes, the
section *Upgrading production* covers the real rollout.

| | Production (unchanged) | Test |
|---|---|---|
| Install | `/home/cbadmin/national_provider_dir/npd-loader` | `/home/cbadmin/national_provider_dir/npd-loader-test` |
| Config | `/etc/npd-loader/config.toml` | `/etc/npd-loader/config-test.toml` |
| Data folder | `/home/cbadmin/national_provider_dir/npd_data` | `/home/cbadmin/national_provider_dir/npd_data_test` |
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
6. 192.10.0.7: about **45 GB** free on the filesystem holding `/home/cbadmin/national_provider_dir/npd_data_test`. That's 2.4 GB of `.zst` plus
   35 GB of `.ndjson` per release.
7. You can connect to 192.10.0.6 with `psql` as a role that can create databases.

Installs run as `cbadmin`, which holds the GitHub token. Placeholders used below: `<pg_admin>` is
the Postgres role that creates databases; `<npd_user>` and `<catalog_user>` are the `user` values in
`[npd_db]` and `[catalog]` of `/etc/npd-loader/config.toml`.

## 1. Install the branch beside production (192.10.0.7)

```bash
# as cbadmin
mkdir -p /home/cbadmin/national_provider_dir/npd-loader-test /home/cbadmin/national_provider_dir/npd_data_test
git clone -b css-catalog-client-migration https://github.com/checkbook-org/npd_fhir_loader.git /home/cbadmin/national_provider_dir/npd-loader-test/src
python3.12 -m venv /home/cbadmin/national_provider_dir/npd-loader-test/.venv
/home/cbadmin/national_provider_dir/npd-loader-test/.venv/bin/pip install /home/cbadmin/national_provider_dir/npd-loader-test/src
/home/cbadmin/national_provider_dir/npd-loader-test/.venv/bin/pip show css-catalog | head -2
```

Expect `Name: css-catalog` and `Version: 0.1.0`. If pip asks for a GitHub username or fails while cloning
css-catalog-client, the token isn't set up for this account. Go back to `github-token-css-catalog.md`.

The loader runs as cbadmin, as production does today, so this runbook runs every loader command as cbadmin and
gives cbadmin ownership of the test config and data folder. When production moves to a dedicated `npd` service
account, change `cbadmin` to `npd` in the `chown` and `sudo -u` lines below, and give `npd` the data folder with
`sudo chown -R npd: /home/cbadmin/national_provider_dir/npd_data_test`. Also give `npd` execute-only access to the
two folders above the install, so it can reach the venv and the data folder without being able to list them:
`sudo setfacl -m u:npd:x /home/cbadmin /home/cbadmin/national_provider_dir`.

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
sudo chown cbadmin: /etc/npd-loader/config-test.toml
sudo chmod 600 /etc/npd-loader/config-test.toml
sudoedit /etc/npd-loader/config-test.toml
```

- `[storage] root = "/home/cbadmin/national_provider_dir/npd_data_test"`
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
T="sudo -u cbadmin /home/cbadmin/national_provider_dir/npd-loader-test/.venv/bin/npd-loader --config /etc/npd-loader/config-test.toml"
$T init-db
$T status
```

`init-db` exits 0. `status` prints only the header line, since there are no releases yet.

## 5. Full run

Run it inside `screen` as cbadmin, so it keeps running if your SSH session drops. The log is also written to a
file:

```bash
screen -S npd-test                      # opens a new shell inside a screen session named npd-test
```

In that shell:

```bash
cd /home/cbadmin/national_provider_dir/npd-loader-test
.venv/bin/npd-loader --config /etc/npd-loader/config-test.toml run 2>&1 | tee run.log
echo "exit ${PIPESTATUS[0]}" | tee -a run.log
```

- **Detach** (leave it running): press `Ctrl-a`, then `d`. You can now log out.
- **Reattach later:** `screen -r npd-test`. If it is the only session, plain `screen -r` works too. `screen -ls`
  lists sessions.
- **Follow the log without reattaching:**
  `tail -f /home/cbadmin/national_provider_dir/npd-loader-test/run.log`
- **When finished:** type `exit` in the screen shell to close the session.

The sequence:
- download: a few minutes
- extract: a few minutes
- raw load
- transforms (`010_practitioner.sql` … `090_identifier.sql`)
- `published release YYYY-MM-DD (27 tables)`
- `run: import: success`

Expect several hours in total; on a workstation it took about 4 h. The run is done when `run.log` ends with
`exit 0`. On a failure it ends with `exit 1`, and the error is a few lines above.

## 6. Verify

```bash
T="sudo -u cbadmin /home/cbadmin/national_provider_dir/npd-loader-test/.venv/bin/npd-loader --config /etc/npd-loader/config-test.toml"
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

**Strongest check:** if production has already loaded the **same release**, every published table must have
the same number of rows in `npd_test` and `npd`. `compare` counts them in both databases and prints one line
per table:

```bash
$T compare /etc/npd-loader/config.toml                       # the newest release published in both
$T compare /etc/npd-loader/config.toml --release YYYY-MM-DD  # or a given release
```

It exits 0 and ends with `N of N tables match` when every count is equal. A table marked `NO` has a different
count, or exists for that release in only one database (`-`), and the exit code is 1. `compare` only reads, but
it counts every row of the release (about 75 GB), so it takes several minutes; run it when neither loader is
running. It reads each database with the `[npd_db]` user of its own config, so the cbadmin in `$T` must be able
to read both config files.

Finally, run again. Both stages must be skipped:

```bash
$T run           # "already downloaded", "already imported", exit 0
```

Check that `master_warehouse_run` still has 3 rows.

## 7. Clean up the test

Once you no longer need it:

```bash
psql -h 192.10.0.6 -U <pg_admin> -d postgres -c 'DROP DATABASE npd_test' -c 'DROP DATABASE css_catalog_test'
rm -rf /home/cbadmin/national_provider_dir/npd_data_test /home/cbadmin/national_provider_dir/npd-loader-test        # as cbadmin
sudo rm /etc/npd-loader/config-test.toml
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
5. `sudo -u cbadmin /home/cbadmin/national_provider_dir/npd-loader/.venv/bin/npd-loader --config /etc/npd-loader/config.toml init-db`, then `... status`.
   It must list the existing releases with their run ids.
6. Optional: a manual `... run`. It downloads and imports a new release if there is one, and otherwise skips
   both stages.
7. `sudo systemctl start npd-loader.timer`.

**Rollback:** `git -C /home/cbadmin/national_provider_dir/npd-loader/src checkout <previous commit>`, repeat step 4's `pip install
--force-reinstall`, then restart the timer. The new code writes the same rows to the same tables, so the old
code reads them without change.
