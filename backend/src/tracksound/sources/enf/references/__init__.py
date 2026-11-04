"""Reference grid-frequency data: built-in public archives (downloaded on demand and cached under
``<cache>/enf/``) and references imported from CSV (kept under ``<data>/enf/references/``).

Every reference is served as a 1 s series with NaN gaps (see ``common.Series``).
"""
from __future__ import annotations

from pathlib import Path

from ....config import Settings
from ....store import NotFound
from . import aemo, elexon, fingrid, imported, neso, pgf
from .common import Progress, RefInfo, Series, SourceUnavailable

__all__ = ["RefInfo", "Series", "SourceUnavailable", "builtin_infos", "list_references", "get_info",
           "get_reference", "load_reference", "import_reference", "delete_reference"]


def builtin_infos() -> list[RefInfo]:
    return [neso.INFO, elexon.INFO, fingrid.INFO, aemo.INFOS["au-nem-aemo"], aemo.INFOS["au-tas-aemo"],
            *(pgf.info_for(c) for c in pgf.CAMPAIGNS)]


_BUILTIN = {i.id: i for i in builtin_infos()}


def _public(info: RefInfo) -> dict:
    doc = info.public()
    doc["available"] = info.available_note
    return doc


def list_references(settings: Settings) -> list[dict]:
    out = [_public(i) for i in builtin_infos()]
    for meta in imported.list_imported(settings.data_dir):
        doc = imported.info_from_meta(meta).public()
        doc["available"] = None
        out.append(doc)
    return out


def get_reference(settings: Settings, ref_id: str) -> dict:
    """One EnfReference document (NotFound if there is no such reference)."""
    info = get_info(settings, ref_id)
    doc = info.public()
    doc["available"] = info.available_note
    return doc


def get_info(settings: Settings, ref_id: str) -> RefInfo:
    if ref_id in _BUILTIN:
        return _BUILTIN[ref_id]
    return imported.info_from_meta(imported.get_imported(settings.data_dir, ref_id))


def _source(cache_root: Path, ref_id: str):
    if ref_id == neso.INFO.id:
        return neso.NesoSource(cache_root)
    if ref_id == elexon.INFO.id:
        return elexon.ElexonSource(cache_root)
    if ref_id == fingrid.INFO.id:
        return fingrid.FingridSource(cache_root)
    if ref_id in aemo.INFOS:
        return aemo.AemoSource(cache_root, ref_id)
    if ref_id in pgf.BY_ID:
        return pgf.PgfSource(cache_root, ref_id)
    raise NotFound(ref_id)


def load_reference(settings: Settings, ref_id: str, t0_utc: float, t1_utc: float,
                   progress: Progress | None = None) -> Series:
    """The reference between t0 and t1 (UTC s) on a 1 s grid, downloading what is missing."""
    progress = progress or (lambda f, m: None)
    info = get_info(settings, ref_id)
    if info.source == "import":
        return imported.load_imported(settings.data_dir, ref_id, t0_utc, t1_utc)
    return _source(settings.cache_dir / "enf", ref_id).load(t0_utc, t1_utc, progress)


def import_reference(settings: Settings, raw: bytes, name: str, grid: str, nominal_hz: int) -> dict:
    meta = imported.import_csv(settings.data_dir, raw, name, grid, nominal_hz)
    doc = imported.info_from_meta(meta).public()
    doc["available"] = None
    return doc


def delete_reference(settings: Settings, ref_id: str) -> None:
    if ref_id in _BUILTIN:
        raise ValueError("built-in references cannot be deleted")
    imported.delete_imported(settings.data_dir, ref_id)
