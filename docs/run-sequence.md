# `npd-loader run` sequence

`run` runs DOWNLOAD and then IMPORT. IMPORT runs EXTRACT itself when any `.ndjson` is missing. IMPORT runs even if
DOWNLOAD failed, because an earlier download may still need importing. The exit code is 1 if either stage failed.
Each stage takes a Postgres advisory lock in `npd`; if another process holds it, the stage logs "nothing to do"
and returns. All catalog writes autocommit on their own short connections, so they survive a failed load.

```mermaid
sequenceDiagram
    autonumber
    participant CLI as cli._run
    participant DL as Download
    participant EX as Extract
    participant IM as Import
    participant CMS as directory.cms.gov / S3
    participant FS as Storage (storage.root)
    participant CAT as css_catalog_local
    participant NPD as npd database

    rect rgba(127,127,127,0.08)
    Note over CLI,NPD: DOWNLOAD stage
    CLI->>DL: run_download()
    DL->>NPD: advisory lock "download"
    DL->>CMS: GET manifest.json (302 to signed S3 URL)
    CMS-->>DL: manifest (release date, file list)
    DL->>CAT: last successful DOWNLOAD for release?
    alt already downloaded
        DL-->>CLI: SKIPPED
    else new release
        DL->>CAT: INSERT master_warehouse_run (DOWNLOAD) RETURNING id
        DL->>CAT: INSERT data_file (manifest)
        DL->>FS: write run_{id}_{ts}/file_{fid}_manifest.json
        loop each .zst in manifest
            DL->>CAT: INSERT data_file (zst, parent = manifest)
            loop until complete or max_attempts (exponential backoff)
                DL->>CMS: resolve fresh signed URL, GET with Range from last byte
                CMS-->>DL: bytes, streamed to .part and hashed
            end
            DL->>FS: rename .part to .zst
            DL->>CAT: UPDATE data_file (size, sha256, dates)
        end
        DL->>CMS: re-fetch manifest (release must be unchanged)
        alt any error
            DL->>CAT: UPDATE run: Failed, result
            DL-->>CLI: StageFailed (logged, run continues)
        else
            DL->>CAT: UPDATE run: Success, xml_output
            DL-->>CLI: SUCCESS
        end
    end
    end

    rect rgba(127,127,127,0.08)
    Note over CLI,NPD: IMPORT stage
    CLI->>IM: run_import()
    IM->>NPD: advisory lock "import"
    IM->>NPD: drop orphaned standalone tables (__r{id}) of killed imports
    IM->>CAT: newest successful DOWNLOAD run
    alt none
        IM-->>CLI: StageFailed "no successful download"
    else already imported
        IM-->>CLI: SKIPPED
    else
        IM->>CAT: completed .ndjson for every .zst of that download?
        opt any .ndjson missing, or size differs on disk
            IM->>EX: run_extract(release)
            EX->>NPD: advisory lock "extract"
            EX->>CAT: INSERT master_warehouse_run (EXTRACT)
            loop each .zst without a valid .ndjson
                EX->>CAT: INSERT data_file (ndjson, parent = zst)
                EX->>FS: zstd-decompress .zst to .part, then rename to .ndjson
                EX->>CAT: UPDATE data_file (size, sha256)
            end
            EX->>CAT: UPDATE run: Success / Failed
            EX-->>IM: SUCCESS (or StageFailed)
        end
        IM->>CAT: INSERT master_warehouse_run (IMPORT)
        IM->>NPD: release already a partition and not --force? then fail
        IM->>FS: read each .ndjson
        IM->>NPD: load_raw: COPY into standalone npd_raw table, validate, index, check duplicates
        IM->>NPD: run_transforms: SQL scripts build standalone npd.* tables
        IM->>NPD: publish (one transaction, lock_timeout, retried):<br/>ATTACH PARTITION for each table, upsert npd.release
        IM->>NPD: ANALYZE new partitions
        alt any error
            IM->>CAT: UPDATE data_file.exceptions (bad file)
            IM->>NPD: DROP this run's standalone tables
            IM->>CAT: UPDATE run: Failed, result
            IM-->>CLI: StageFailed
        else
            IM->>CAT: UPDATE data_file.date_loaded for each .ndjson
            Note over IM,NPD: retention: keep newest keep_releases published releases
            IM->>NPD: DETACH and DROP partitions of older releases
            IM->>FS: delete .ndjson of dropped or stale releases
            IM->>CAT: UPDATE run: Success (retention warnings in result)
            IM-->>CLI: SUCCESS
        end
    end
    end

    CLI-->>CLI: exit 1 if either stage failed, else 0
```
