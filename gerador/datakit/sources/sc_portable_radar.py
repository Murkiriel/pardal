"""SIE-SC — locais de operação do radar portátil nas rodovias estaduais de Santa Catarina, postos na via pelo OSM.

SC não tem radar fixo estadual (lei de 2012); a SIE publica onde a polícia militar rodoviária opera o radar portátil: um
PDF com código, rodovia SC, km, sentido (crescente ou decrescente; "DESCRESCENTE" também aparece), município e limite,
sem coordenada (medido em 2026-10-09: 596 pontos, válido desde 16/05/2023). Sem malha estadual com km em aberto, o km
cai entre os dois marcos quilométricos do OSM (`highway=milestone` com `ref` e `distance`) que o cercam na mesma
rodovia, cada um a até MAX_GAP_KM; o trecho entre os dois, pelo caminho das vias `ref=SC-N` (e não pela reta), entra
no radar_portatil.csv como os trechos aptos da PRF: road "SC-N", sem as multas. Pontos no mesmo intervalo de marcos
viram um trecho só. Medido: 462 dos 596 pontos ficam entre dois marcos da rodovia, 327 com os dois a até 5 km.
"""
from __future__ import annotations

import heapq
import io
import re
from collections import defaultdict
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from datakit.common import RadarZone, haversine_m
from datakit.radar_zones import SIMPLIFY_M, simplify
from datakit.sources._http import get_bytes

PDF_URL = "https://www.sie.sc.gov.br/webdocs/sie/consultamultas/radares/Locais_de_Operacao_Radares_Portateis.pdf"
MAX_GAP_KM = 5.0
SNAP_M = 200.0

LatLng = Tuple[float, float]
NodeList = List[Tuple[int, float, float]]   # (id do nó, lat, lng), na ordem da via

_POINT = re.compile(r"P-\d+\s+\d{6}\s+ROD\.\s*SC\s*(\d+)\s+KM\s+([\d.]+)\s+(\w+)")
_VALID = re.compile(r"A\s+PARTIR\s+(?:DE\s+)?(\d{2})[/-](\d{2})[/-](\d{2,4})", re.I)
_SC_REF = re.compile(r"SC[-\s]?(\d+)")


@dataclass(frozen=True)
class Point:
    """Um local de operação: rodovia SC, km e sentido do km (None: não dito)."""
    road: int
    km: float
    increasing: Optional[bool]


def parse(text: str) -> List[Point]:
    """Os locais do texto do PDF (as linhas às vezes vêm coladas: "...80 88P-01 001002 ROD. SC401 ...")."""
    out: List[Point] = []
    for road, km, sense in _POINT.findall(text):
        s = sense.upper()
        increasing = True if s == "CRESCENTE" else (False if s in ("DECRESCENTE", "DESCRESCENTE") else None)
        out.append(Point(int(road), float(km), increasing))
    return out


def valid_from(text: str) -> str:
    """O início da validade (AAAA-MM-DD) se o texto disser "a partir de DD/MM/AA"; senão vazio."""
    m = _VALID.search(text)
    if not m:
        return ""
    year = m.group(3) if len(m.group(3)) == 4 else "20" + m.group(3)
    return f"{year}-{m.group(2)}-{m.group(1)}"


def _km(value: Optional[str]) -> Optional[float]:
    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return None


def load_osm(pbf: str) -> Tuple[Dict[int, List[Tuple[float, float, float]]], Dict[int, List[NodeList]]]:
    """({rodovia: [(km, lat, lng)] dos marcos, por km}, {rodovia: [vias como listas de nós]}) das SC do extrato."""
    import osmium
    import osmium.filter

    marks: Dict[int, Dict[float, Tuple[float, float, float]]] = defaultdict(dict)
    for o in osmium.FileProcessor(pbf).with_filter(osmium.filter.TagFilter(("highway", "milestone"))):
        if not o.is_node() or not o.location.valid():
            continue
        km = _km(o.tags.get("distance"))
        m = _SC_REF.search(o.tags.get("ref") or "")
        if km is not None and m:
            marks[int(m.group(1))].setdefault(km, (km, o.location.lat, o.location.lon))
    ways: Dict[int, List[NodeList]] = defaultdict(list)
    for o in osmium.FileProcessor(pbf).with_locations().with_filter(osmium.filter.KeyFilter("ref")):
        if not o.is_way() or "highway" not in o.tags:
            continue
        roads = {int(r) for r in _SC_REF.findall(o.tags.get("ref") or "")}
        if not roads:
            continue
        nodes = [(n.ref, n.location.lat, n.location.lon) for n in o.nodes if n.location.valid()]
        for road in roads:
            ways[road].append(nodes)
    return {road: sorted(byk.values()) for road, byk in marks.items()}, dict(ways)


def road_path(ways: Sequence[NodeList], a: LatLng, b: LatLng) -> List[LatLng]:
    """O caminho mais curto pelas vias da rodovia entre os nós mais perto de `a` e de `b` (a até SNAP_M); vazio sem."""
    pos: Dict[int, LatLng] = {}
    graph: Dict[int, List[Tuple[int, float]]] = defaultdict(list)
    for way in ways:
        for (n1, la1, lo1), (n2, la2, lo2) in zip(way, way[1:]):
            d = haversine_m((la1, lo1), (la2, lo2))
            graph[n1].append((n2, d))
            graph[n2].append((n1, d))
            pos[n1], pos[n2] = (la1, lo1), (la2, lo2)
    if not pos:
        return []

    def snap(p: LatLng) -> Optional[int]:
        node, dist = min(((n, haversine_m(p, q)) for n, q in pos.items()), key=lambda x: x[1])
        return node if dist <= SNAP_M else None
    start, goal = snap(a), snap(b)
    if start is None or goal is None:
        return []
    best = {start: 0.0}
    back: Dict[int, int] = {}
    queue = [(0.0, start)]
    while queue:
        d, n = heapq.heappop(queue)
        if n == goal:
            break
        if d > best.get(n, float("inf")):
            continue
        for m, w in graph[n]:
            if d + w < best.get(m, float("inf")):
                best[m], back[m] = d + w, n
                heapq.heappush(queue, (d + w, m))
    if goal not in best:
        return []
    path = [goal]
    while path[-1] != start:
        path.append(back[path[-1]])
    return [pos[n] for n in reversed(path)]


def place(points: Sequence[Point], marks: Dict[int, List[Tuple[float, float, float]]],
          ways: Dict[int, List[NodeList]], valid: str) -> Tuple[List[RadarZone], int]:
    """(trechos entre os marcos que cercam cada ponto, pontos que ficaram de fora)."""
    zones: Dict[Tuple[int, float, float], RadarZone] = {}
    unplaced = 0
    for p in points:
        road_marks = marks.get(p.road, [])
        below = [m for m in road_marks if m[0] <= p.km]
        above = [m for m in road_marks if m[0] > p.km]
        if not below or not above or p.km - below[-1][0] > MAX_GAP_KM or above[0][0] - p.km > MAX_GAP_KM:
            unplaced += 1
            continue
        lo, hi = below[-1], above[0]
        key = (p.road, lo[0], hi[0])
        if key in zones:
            continue
        path = road_path(ways.get(p.road, []), (lo[1], lo[2]), (hi[1], hi[2]))
        if len(path) < 2:
            unplaced += 1
            continue
        zones[key] = RadarZone(f"SC-{p.road}", lo[0], hi[0], valid, tuple(simplify(path, SIMPLIFY_M)))
    return list(zones.values()), unplaced


def zones(pbf: str) -> Tuple[List[RadarZone], int]:
    """(trechos, pontos de fora) do PDF da SIE, com os marcos e as vias do extrato `pbf` (o da região sul)."""
    import pypdf

    text = "\n".join(page.extract_text() or "" for page in pypdf.PdfReader(io.BytesIO(get_bytes(PDF_URL))).pages)
    marks, ways = load_osm(pbf)
    return place(parse(text), marks, ways, valid_from(text))
