"""Polígono real de cada UF (malha estadual do IBGE), para recorte exato — opcional.

Precisa de `shapely` e do GeoJSON `br_states.json` (malha do IBGE espelhada em
github.com/giuliano-macedo/geodata-br-states, MIT). Sem qualquer um dos dois, `polygons()`
devolve {} e o build cai no recorte por bbox.
"""
from __future__ import annotations

import json
import os
from typing import Dict, Optional

MESH_URL = "https://raw.githubusercontent.com/giuliano-macedo/geodata-br-states/main/geojson/br_states.json"

_cache: Optional[dict] = None


def _ensure_mesh(raw_dir: str) -> Optional[str]:
    path = os.path.join(raw_dir, "br_states.json")
    if not os.path.exists(path):
        try:
            os.makedirs(raw_dir, exist_ok=True)
            from datakit.sources._http import get_bytes
            data = get_bytes(MESH_URL)
            with open(path, "wb") as f:
                f.write(data)
        except Exception as e:  # noqa: BLE001
            from datakit import falhas
            falhas.registrar("IBGE (malha das UFs; recorte por bbox)", e)
            return None
    return path


def polygons(raw_dir: str) -> Dict[str, object]:
    """{UF: shapely geometry}. {} se shapely ou a malha não estiverem disponíveis."""
    global _cache
    if _cache is not None:
        return _cache
    try:
        from shapely.geometry import shape
    except Exception:  # noqa: BLE001
        print("[ufpoly] shapely não instalado; recorte por bbox")
        _cache = {}
        return _cache
    path = _ensure_mesh(raw_dir)
    if path is None:
        _cache = {}
        return _cache
    with open(path, encoding="utf-8") as f:
        gj = json.load(f)
    out: Dict[str, object] = {}
    for ft in gj.get("features", []):
        props = ft.get("properties", {})
        uf = props.get("SIGLA") or props.get("sigla") or props.get("PK_sigla")
        if not uf:
            continue
        try:
            out[uf.upper()] = shape(ft["geometry"]).buffer(0)  # buffer(0) conserta anéis
        except Exception:  # noqa: BLE001
            continue
    _cache = out
    print(f"[ufpoly] {len(out)} polígonos de UF carregados")
    return out
