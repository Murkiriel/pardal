"""tiles -> pacote por UF (a unidade que se baixa por estado).

Lê os tiles que tocam a bbox da UF, recorta na bbox, deduplica de novo (um radar na
divisa pode cair em dois tiles) e escreve data/packs/<UF>/ com manifest.json.
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
from datetime import datetime, timezone
from typing import List, Tuple

from datakit.build_tiles import TILE_DEG, tile_of
from datakit.common import Camera, CameraKind, Limit, Struct, in_bbox, merge_cameras, merge_limits
from datakit.common.model import parse_dir
from datakit.common.ufs import uf_bbox


def build(uf: str, tiles_dir: str, packs_dir: str, index: dict, poly=None) -> dict:
    """`poly` (shapely geometry, opcional): recorte exato por polígono da UF. Sem ele,
    só a bbox — some radar de UF vizinha que cai no retângulo."""
    uf = uf.upper()
    bbox = uf_bbox(uf)
    (mnla, mnlo, mxla, mxlo) = bbox
    r0, c0 = tile_of(mnla, mnlo)
    r1, c1 = tile_of(mxla, mxlo)

    keep = _make_keep(bbox, poly)

    cams: List[Camera] = []
    lims: List[Limit] = []
    structs: List[Struct] = []
    tiles_used: List[List[int]] = []
    for row in range(min(r0, r1), max(r0, r1) + 1):
        for col in range(min(c0, c1), max(c0, c1) + 1):
            tdir = os.path.join(tiles_dir, str(row), str(col))
            if not os.path.isdir(tdir):
                continue
            tiles_used.append([row, col])
            cams += [c for c in _read_cameras(os.path.join(tdir, "cameras.csv")) if keep(c.lat, c.lng)]
            lims += [x for x in _read_limits(os.path.join(tdir, "limits.csv")) if keep(x.lat, x.lng)]
            structs += [x for x in _read_structs(os.path.join(tdir, "structs.csv"))
                        if keep(x.lat1, x.lng1) or keep(x.lat2, x.lng2)]

    cams = merge_cameras(cams)
    lims = merge_limits(lims)
    structs = _dedupe_structs(structs)

    out = os.path.join(packs_dir, uf)
    os.makedirs(out, exist_ok=True)
    cfile = os.path.join(out, "cameras.csv")
    lfile = os.path.join(out, "limits.csv")
    sfile = os.path.join(out, "structs.csv")
    _write_csv(cfile, Camera.HEADER, (c.row() for c in cams))
    _write_csv(lfile, Limit.HEADER, (x.row() for x in lims))
    _write_csv(sfile, Struct.HEADER, (x.row() for x in structs))

    manifest = {
        "schema": index.get("schema", 1),
        "uf": uf,
        "bbox": list(bbox),
        "built_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "artifact_built_at": index.get("built_at"),
        "sources": index.get("sources", []),
        "tiles": tiles_used,
        "counts": {
            "cameras": len(cams),
            "cameras_with_limit": sum(1 for c in cams if c.limit_kmh is not None),
            "cameras_inactive": sum(1 for c in cams if not c.active),
            "sections": sum(1 for c in cams if c.kind == CameraKind.SECTION),
            "red_lights": sum(1 for c in cams if c.kind == CameraKind.RED_LIGHT),
            "limits": len(lims),
        },
        "files": {
            "cameras.csv": {"sha256": _sha256(cfile), "bytes": os.path.getsize(cfile)},
            "limits.csv": {"sha256": _sha256(lfile), "bytes": os.path.getsize(lfile)},
            "structs.csv": {"sha256": _sha256(sfile), "bytes": os.path.getsize(sfile)},
        },
    }
    manifest["counts"]["structs"] = len(structs)
    with open(os.path.join(out, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    return manifest


def _make_keep(bbox, poly):
    """Filtro ponto-a-ponto: bbox sempre; polígono também se disponível (usa prepared
    geometry pra ficar rápido)."""
    if poly is None:
        return lambda lat, lng: in_bbox(bbox, lat, lng)
    try:
        from shapely.geometry import Point
        from shapely.prepared import prep
        pg = prep(poly)
        return lambda lat, lng: in_bbox(bbox, lat, lng) and pg.contains(Point(lng, lat))
    except Exception:  # noqa: BLE001
        return lambda lat, lng: in_bbox(bbox, lat, lng)


def _read_cameras(path: str) -> List[Camera]:
    if not os.path.exists(path):
        return []
    out = []
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            out.append(Camera(
                lat=float(r["lat"]), lng=float(r["lng"]),
                kind=CameraKind(r["kind"]),
                limit_kmh=int(r["limit_kmh"]) if r.get("limit_kmh") else None,
                source=r["source"], active=r.get("active", "1") != "0",
                end_lat=float(r["end_lat"]) if r.get("end_lat") else None,
                end_lng=float(r["end_lng"]) if r.get("end_lng") else None,
                direction_deg=parse_dir(r.get("direction_deg")),
            ))
    return out


def _read_limits(path: str) -> List[Limit]:
    if not os.path.exists(path):
        return []
    out = []
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            out.append(Limit.from_row(r))
    return out


def _read_structs(path: str) -> List[Struct]:
    if not os.path.exists(path):
        return []
    out = []
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            out.append(Struct(float(r["lat1"]), float(r["lng1"]), float(r["lat2"]), float(r["lng2"]), r["kind"]))
    return out


def _dedupe_structs(structs: List[Struct]) -> List[Struct]:
    best = {}
    for s in structs:
        best[(round(s.lat1, 5), round(s.lng1, 5), round(s.lat2, 5), round(s.lng2, 5))] = s
    return sorted(best.values(), key=lambda x: (x.lat1, x.lng1))


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
