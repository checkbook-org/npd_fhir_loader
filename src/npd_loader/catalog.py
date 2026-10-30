"""Run and file tracking. `Catalog` is NPD's interface; NpdCatalog adapts the shared css_catalog library to it."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Iterable, Protocol

import css_catalog
from css_catalog import DataFile

from npd_loader.config import CatalogConfig
from npd_loader.runxml import parse_release

__all__ = ["SUCCESS", "FAILED", "Run", "DataFile", "Catalog", "NPD_FILE_FIELDS", "NpdCatalog", "open_catalog",
           "newest_run", "releases_of"]

SUCCESS = css_catalog.SUCCESS
FAILED = css_catalog.FAILED

# file_set and source_version_name come from [catalog] config, never from callers.
NPD_FILE_FIELDS: tuple[str, ...] = tuple(
    f for f in css_catalog.DATA_FILE_FIELDS if f not in ("file_set", "source_version_name"))


@dataclass(frozen=True)
class Run:
    id: int
    run_class: str
    description: str
    config_xml: str
    started_at: datetime
    release_date: date | None
    status: str | None = None


def newest_run(runs: Iterable[Run], release: date | None) -> Run | None:
    candidates = [r for r in runs if r.release_date is not None and (release is None or r.release_date == release)]
    return max(candidates, key=lambda r: (r.release_date, r.id), default=None)


def releases_of(runs: Iterable[Run]) -> list[date]:
    return sorted({r.release_date for r in runs if r.release_date is not None})


class Catalog(Protocol):
    def start_run(self, run_class: str, description: str, config_xml: str) -> Run: ...
    def finish_run(self, run: Run, status: str, result: str | None = None, output_xml: str | None = None) -> None: ...
    def add_data_file(self, run: Run, **fields: object) -> int: ...
    def update_data_file(self, file_id: int, **fields: object) -> None: ...
    def get_data_files(self, release: date, file_type: str, run_id: int | None = None) -> list[DataFile]: ...
    def last_successful_run(self, run_class: str, release: date | None = None) -> Run | None: ...
    def successful_releases(self, run_class: str) -> list[date]: ...


def _now() -> datetime:
    return datetime.now().replace(microsecond=0)


def _check_fields(fields: dict[str, object]) -> None:
    unknown = set(fields) - set(NPD_FILE_FIELDS)
    if unknown:
        raise TypeError(f"unknown data_file fields: {sorted(unknown)}")


class NpdCatalog:
    """NPD's Catalog interface over a css_catalog catalog, scoped to the configured project and run type."""

    def __init__(self, inner: css_catalog.Catalog, cfg: CatalogConfig):
        self._inner = inner
        self._cfg = cfg

    def start_run(self, run_class: str, description: str, config_xml: str) -> Run:
        started = _now()
        run = self._inner.start_run(project=self._cfg.project, run_type=self._cfg.run_type, run_class=run_class,
                                    description=description, config=config_xml, started_at=started)
        return Run(run.id, run_class, description, config_xml, started, parse_release(config_xml))

    def finish_run(self, run: Run, status: str, result: str | None = None, output_xml: str | None = None) -> None:
        self._inner.finish_run(run.id, status, result=result, output_xml=output_xml, completed_at=_now())

    def add_data_file(self, run: Run, **fields: object) -> int:
        _check_fields(fields)
        return self._inner.add_file(run.id, file_set=self._cfg.file_set,
                                    source_version_name=self._cfg.source_version_name, **fields)

    def update_data_file(self, file_id: int, **fields: object) -> None:
        _check_fields(fields)
        if not fields:
            return
        self._inner.update_file(file_id, **fields)

    def get_data_files(self, release: date, file_type: str, run_id: int | None = None) -> list[DataFile]:
        return self._inner.find_files(file_set=self._cfg.file_set, source_version_num=release.isoformat(),
                                      file_type=file_type, run_id=run_id)

    def _successful_runs(self, run_class: str) -> list[Run]:
        runs = self._inner.find_runs(project=self._cfg.project, run_type=self._cfg.run_type, run_class=run_class,
                                     status=SUCCESS)
        return [Run(r.id, run_class, r.description or "", r.config_xml or "", r.started_at,
                    parse_release(r.config_xml), SUCCESS) for r in runs]

    def last_successful_run(self, run_class: str, release: date | None = None) -> Run | None:
        return newest_run(self._successful_runs(run_class), release)

    def successful_releases(self, run_class: str) -> list[date]:
        return releases_of(self._successful_runs(run_class))


_BACKENDS = {"css_catalog_pg": "postgres", "mssql": "mssql"}


def open_catalog(cfg: CatalogConfig, connection: str) -> NpdCatalog:
    inner = css_catalog.connect(_BACKENDS[cfg.backend], connection, run_table=cfg.run_table,
                                file_table=cfg.file_table, status_success=cfg.status_success,
                                status_failed=cfg.status_failed)
    return NpdCatalog(inner, cfg)
