"""ANTT — dataset "Radar" (concessões federais).

CSV com latitude/longitude prontos, delimitado por ';', encoding latin-1. Só os 'Ativo'.
'velocidade_leve' vira o limite (é o do veículo leve, serve pra moto). source="ANTT".
'sentido' (Crescente/Decrescente) vira direction_deg pela rota do SNV da BR (ver
common/sentido.py); "Crescente/Decrescente" fica vazio (os dois sentidos).
"""
from __future__ import annotations

import csv
import io
import re
from typing import Callable, List, Optional, Tuple

from datakit.contexto import Carga, Contexto
from datakit.common import Camera, CameraKind, Limit, in_bbox
from datakit.common.lrs import SnvRoutes
from datakit.common.sentido import direction_on, parse_sentido
from datakit.sources import snv
from datakit.sources._http import ckan_resources, get_bytes, in_br, newest, to_float

SNAP_M = 150.0

CKAN = "https://dados.antt.gov.br/api/3/action/package_show?id=radar"


def load(raw_dir: str, bbox: Optional[Tuple[float, float, float, float]] = None,
         routes: Optional[Callable[[], Optional[SnvRoutes]]] = None) -> Tuple[List[Camera], List[Limit]]:
    """`routes`: devolve as rotas do SNV (para o sentido); sem ele, carrega do raw_dir."""
    res = newest(ckan_resources(CKAN), "CSV", "dados dos radares")
    text = get_bytes(res["url"]).decode("latin-1")
    reader = csv.reader(io.StringIO(text), delimiter=";")
    header = [h.strip().lower() for h in next(reader)]
    idx = {name: header.index(name) for name in
           ("latitude", "longitude", "situacao", "velocidade_leve", "rodovia", "uf", "sentido")
           if name in header}
    if not {"latitude", "longitude", "situacao"} <= idx.keys():
        raise RuntimeError(f"colunas ANTT inesperadas: {header}")

    get_routes = routes or (lambda: snv.load_routes(raw_dir))
    snv_routes = get_routes() if "sentido" in idx else None
    out: List[Camera] = []
    for row in reader:
        if len(row) <= max(idx.values()):
            continue
        if row[idx["situacao"]].strip().lower() != "ativo":
            continue
        lat, lng = to_float(row[idx["latitude"]]), to_float(row[idx["longitude"]])
        if lat is None or lng is None or not in_br(lat, lng) or not in_bbox(bbox, lat, lng):
            continue
        limit = None
        if "velocidade_leve" in idx:
            digits = re.sub(r"\D", "", row[idx["velocidade_leve"]])
            if digits:
                limit = int(digits)
        direction = None
        if snv_routes is not None:
            direction = _direction(snv_routes, row, idx, lat, lng)
        out.append(Camera(round(lat, 6), round(lng, 6), CameraKind.FIXED, limit, "ANTT", True,
                          direction_deg=direction))
    return out, []


def _direction(routes, row, idx, lat: float, lng: float) -> Optional[int]:
    increasing = parse_sentido(row[idx["sentido"]])
    m = re.search(r"(\d{2,3})", row[idx["rodovia"]]) if "rodovia" in idx else None
    if increasing is None or m is None or "uf" not in idx:
        return None
    hit = routes.nearest(int(m.group(1)), row[idx["uf"]].strip(), (lat, lng), SNAP_M)
    if hit is None:
        return None
    line, km, _ = hit
    return direction_on(line, km, increasing)


def carregar(ctx: Contexto) -> Carga:
    """Contrato das fontes (datakit/contexto.py); as rotas do SNV vêm do contexto (uma carga só)."""
    return Carga(*load(ctx.raw_dir, routes=ctx.rotas_snv))
