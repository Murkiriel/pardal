"""Listas da UF -> pacote por UF (a unidade que se baixa por estado).

Recebe os radares, limites e estruturas já atribuídos à UF (build.py, common/ufassign.py),
deduplica de novo por segurança e escreve data/packs/<UF>/ (radares.csv, limites.csv,
estruturas.csv, com os mesmos nomes do que é publicado, e manifesto.json). Antes passava por
tiles gravados em data/tiles e relidos; os tiles de um build antigo que o novo não reescrevia
ficavam lá e voltavam para o pacote.
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
import os
from datetime import datetime, timezone
from typing import Iterable, List, Optional, Tuple

from datakit.common import Bump, Camera, CameraKind, Limit, RadarZone, Struct, Toll, merge_cameras, merge_limits
from datakit.common.ufs import uf_bbox

SCHEMA = 5  # 1 = base (radares+limites); 2 = + estruturas.csv (ponte/túnel); 3 = + lombadas.csv; 4 = + pedagios.csv;
# 5 = + radar_portatil.csv (trechos aptos ao radar portátil da PRF).
# Leitor tolera ausência.
# Nomes dos arquivos: os mesmos do pacote ao repositório publicado (estados/<UF>/radares.csv...).
CAMERAS_FILE = "radares.csv"
LIMITS_FILE = "limites.csv"
STRUCTS_FILE = "estruturas.csv"
BUMPS_FILE = "lombadas.csv"
TOLLS_FILE = "pedagios.csv"
RADAR_ZONES_FILE = "radar_portatil.csv"
MANIFEST_FILE = "manifesto.json"
TILE_DEG = 0.25  # só para o campo "tiles" do manifest (quadrículas de 0,25° com dados)


def tile_of(lat: float, lng: float) -> Tuple[int, int]:
    return int(math.floor((lat + 90.0) / TILE_DEG)), int(math.floor((lng + 180.0) / TILE_DEG))


def tile_bbox(row: int, col: int) -> Tuple[float, float, float, float]:
    min_lat = row * TILE_DEG - 90.0
    min_lng = col * TILE_DEG - 180.0
    return (min_lat, min_lng, min_lat + TILE_DEG, min_lng + TILE_DEG)


def build(uf: str, packs_dir: str, cams: Iterable[Camera], lims: Iterable[Limit],
          structs: Iterable[Struct], sources: List[dict], built_at: Optional[str] = None,
          bumps: Iterable[Bump] = (), tolls: Iterable[Toll] = (),
          radar_zones: Iterable[RadarZone] = ()) -> dict:
    """`built_at`: quando as listas foram montadas (vai como `artifact_built_at`)."""
    uf = uf.upper()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    cams = merge_cameras(cams)
    lims = merge_limits(lims)
    structs = _dedupe_structs(list(structs))
    bump_list = _dedupe_bumps(list(bumps))
    toll_list = sorted(tolls, key=lambda x: (x.lat, x.lng))
    zone_list = sorted(radar_zones, key=lambda z: (z.road, z.km_from))
    tiles = sorted({tile_of(c.lat, c.lng) for c in cams} | {tile_of(x.lat, x.lng) for x in lims}
                   | {tile_of(s.lat1, s.lng1) for s in structs})

    out = os.path.join(packs_dir, uf)
    os.makedirs(out, exist_ok=True)
    cfile = os.path.join(out, CAMERAS_FILE)
    lfile = os.path.join(out, LIMITS_FILE)
    sfile = os.path.join(out, STRUCTS_FILE)
    bfile = os.path.join(out, BUMPS_FILE)
    tfile = os.path.join(out, TOLLS_FILE)
    zfile = os.path.join(out, RADAR_ZONES_FILE)
    _write_csv(cfile, Camera.HEADER, (c.row() for c in cams))
    _write_csv(lfile, Limit.HEADER, (x.row() for x in lims))
    _write_csv(sfile, Struct.HEADER, (x.row() for x in structs))
    _write_csv(bfile, Bump.HEADER, (x.row() for x in bump_list))
    _write_csv(tfile, Toll.HEADER, (x.row() for x in toll_list))
    _write_csv(zfile, RadarZone.HEADER, (z.row() for z in zone_list))

    manifest: dict = {
        "schema": SCHEMA,
        "uf": uf,
        "bbox": list(uf_bbox(uf)),
        "built_at": now,
        "artifact_built_at": built_at or now,
        "sources": sources,
        "tiles": [list(t) for t in tiles],
        "counts": {
            "cameras": len(cams),
            "cameras_with_limit": sum(1 for c in cams if c.limit_kmh is not None),
            "cameras_inactive": sum(1 for c in cams if not c.active),
            "sections": sum(1 for c in cams if c.kind == CameraKind.SECTION),
            "red_lights": sum(1 for c in cams if c.kind == CameraKind.RED_LIGHT),
            "limits": len(lims),
        },
        "files": {
            CAMERAS_FILE: {"sha256": _sha256(cfile), "bytes": os.path.getsize(cfile)},
            LIMITS_FILE: {"sha256": _sha256(lfile), "bytes": os.path.getsize(lfile)},
            STRUCTS_FILE: {"sha256": _sha256(sfile), "bytes": os.path.getsize(sfile)},
            BUMPS_FILE: {"sha256": _sha256(bfile), "bytes": os.path.getsize(bfile)},
            TOLLS_FILE: {"sha256": _sha256(tfile), "bytes": os.path.getsize(tfile)},
            RADAR_ZONES_FILE: {"sha256": _sha256(zfile), "bytes": os.path.getsize(zfile)},
        },
    }
    manifest["counts"]["structs"] = len(structs)
    manifest["counts"]["bumps"] = len(bump_list)
    manifest["counts"]["tolls"] = len(toll_list)
    manifest["counts"]["portable_radar"] = len(zone_list)
    with open(os.path.join(out, MANIFEST_FILE), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    return manifest


def _dedupe_structs(structs: List[Struct]) -> List[Struct]:
    best = {}
    for s in structs:
        best[(round(s.lat1, 5), round(s.lng1, 5), round(s.lat2, 5), round(s.lng2, 5))] = s
    return sorted(best.values(), key=lambda x: (x.lat1, x.lng1))


def _dedupe_bumps(bumps: List[Bump]) -> List[Bump]:
    """Uma por ponto (5 casas, ~1 m): o mesmo nó em duas regiões vizinhas ou repetido no OSM."""
    best = {}
    for b in bumps:
        best[(round(b.lat, 5), round(b.lng, 5))] = b
    return sorted(best.values(), key=lambda x: (x.lat, x.lng))


def _write_csv(path, header, rows) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(header)
        w.writerows(rows)


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()
