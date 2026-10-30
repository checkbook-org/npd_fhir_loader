"""In-memory Catalog for stage unit tests: NpdCatalog over css_catalog's MemoryCatalog."""
from __future__ import annotations

from datetime import date

from css_catalog import MemoryCatalog

from npd_loader.catalog import SUCCESS, NpdCatalog, Run
from npd_loader.config import CatalogConfig
from npd_loader.runxml import build_config_xml


class FakeCatalog(NpdCatalog):
    def __init__(self, cfg: CatalogConfig | None = None) -> None:
        if cfg is None:
            from helpers import config_data  # helpers imports this module
            from npd_loader.config import parse_config
            cfg = parse_config(config_data("/tmp", "https://x/manifest.json")).catalog
        self._memory = MemoryCatalog(status_success=cfg.status_success, status_failed=cfg.status_failed,
                                     first_run_id=62000, first_file_id=12000)
        super().__init__(self._memory, cfg)

    @property
    def runs(self) -> dict[int, dict]:
        return self._memory.runs

    @property
    def files(self) -> dict[int, dict]:
        return self._memory.files

    # ---- test helpers -------------------------------------------------
    def add_successful_run(self, run_class: str, release: date) -> Run:
        run = self.start_run(run_class, "test", build_config_xml({"release_date": release.isoformat()}))
        self.finish_run(run, SUCCESS)
        return run
