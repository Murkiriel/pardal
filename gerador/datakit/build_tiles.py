"""Linhas mescladas -> tiles numa grade fixa + index.json.

Passo intermediário: build_pack monta cada estado a partir dos tiles que cruzam o polígono
da UF, sem precisar manter o país inteiro em memória.
"""
from __future__ import annotations

import csv
import json
import math
import os
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Tuple

from datakit.common import Camera, Limit, Struct

SCHEMA = 2  # 1 = base (cameras+limits); 2 = + structs.csv (ponte/túnel). Reader tolera ausência.
TILE_DEG = 0.25


def tile_of(lat: float, lng: float) -> Tuple[int, int]:
    row = int(math.floor((lat + 90.0) / TILE_DEG))
    col = int(math.floor((lng + 180.0) / TILE_DEG))
    return row, col


def tile_bbox(row: int, col: int) -> Tuple[float, float, float, float]:
    min_lat = row * TILE_DEG - 90.0
    min_lng = col * TILE_DEG - 180.0
    return (min_lat, min_lng, min_lat + TILE_DEG, min_lng + TILE_DEG)


def write_tiles(
    cams: Iterable[Camera],
    lims: Iterable[Limit],
    out_dir: str,
    sources: List[dict],
    sample_m: int,
    ufs: Dict[str, dict] | None = None,
    structs: Iterable[Struct] | None = None,
) -> dict:
    by_cam: Dict[Tuple[int, int], List[Camera]] = {}
    by_lim: Dict[Tuple[int, int], List[Limit]] = {}
    by_str: Dict[Tuple[int, int], List[Struct]] = {}
    for c in cams:
        by_cam.setdefault(tile_of(c.lat, c.lng), []).append(c)
    for lim in lims:
        by_lim.setdefault(tile_of(lim.lat, lim.lng), []).append(lim)
    for s in structs or ():
        by_str.setdefault(tile_of(s.lat1, s.lng1), []).append(s)

    os.makedirs(out_dir, exist_ok=True)
    tiles_meta: List[dict] = []
    for key in sorted(set(by_cam) | set(by_lim) | set(by_str)):
        row, col = key
        tdir = os.path.join(out_dir, str(row), str(col))
        os.makedirs(tdir, exist_ok=True)
        cl = sorted(by_cam.get(key, []), key=lambda c: (c.lat, c.lng))
        ll = sorted(by_lim.get(key, []), key=lambda x: (x.lat, x.lng))
        sl = sorted(by_str.get(key, []), key=lambda x: (x.lat1, x.lng1))
        _write_csv(os.path.join(tdir, "cameras.csv"), Camera.HEADER, (c.row() for c in cl))
        _write_csv(os.path.join(tdir, "limits.csv"), Limit.HEADER, (x.row() for x in ll))
        if sl:
            _write_csv(os.path.join(tdir, "structs.csv"), Struct.HEADER, (x.row() for x in sl))
        tiles_meta.append({"row": row, "col": col, "cameras": len(cl), "limits": len(ll),
                           "structs": len(sl)})

    index = {
        "schema": SCHEMA,
        "tile_deg": TILE_DEG,
        "sample_m": sample_m,
        "built_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "sources": sources,
        "tiles": tiles_meta,
        "ufs": ufs or {},
    }
    with open(os.path.join(out_dir, "index.json"), "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=2)
    return index


def _write_csv(path: str, header: Tuple[str, ...], rows: Iterable[List[str]]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(header)
        w.writerows(rows)
