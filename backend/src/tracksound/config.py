"""Where TrackSound keeps its data on this machine."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def default_data_dir() -> Path:
    return Path(os.environ.get("TRACKSOUND_DATA", Path.home() / ".tracksound")).expanduser()


@dataclass(frozen=True)
class Settings:
    data_dir: Path

    @property
    def cases_dir(self) -> Path:
        return self.data_dir / "cases"

    @property
    def cache_dir(self) -> Path:
        """Downloaded public data (flight days, terrain tiles, lightning files, grid frequency)."""
        return self.data_dir / "cache"

    @property
    def flights_dir(self) -> Path:
        return self.cache_dir / "flights"

    def ensure(self) -> "Settings":
        for p in (self.data_dir, self.cases_dir, self.cache_dir, self.flights_dir):
            p.mkdir(parents=True, exist_ok=True)
        # dopplertrack keeps Doc 8643 and terrain tiles in its own cache; keep them with ours
        os.environ.setdefault("DOPPLERTRACK_CACHE", str(self.cache_dir / "dopplertrack"))
        Path(os.environ["DOPPLERTRACK_CACHE"]).mkdir(parents=True, exist_ok=True)
        return self


def load_settings(data_dir: str | Path | None = None) -> Settings:
    return Settings(Path(data_dir).expanduser() if data_dir else default_data_dir()).ensure()
