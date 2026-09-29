"""Linha de radar / linha de limite + merge/dedupe.

Mescla por coordenada: arredonda a
~1 m (5 casas decimais) e, no empate, mantém a linha com limite; depois a ativa.

`direction_deg` (radar e limite): rumo, em graus a partir do norte, do trânsito que o radar
fiscaliza ou a que a placa se aplica. Vazio = os dois sentidos ou sentido desconhecido.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Iterable, List, Optional, Tuple


class CameraKind(str, Enum):
    FIXED = "FIXED"
    SECTION = "SECTION"
    RED_LIGHT = "RED_LIGHT"


@dataclass(frozen=True)
class Camera:
    lat: float
    lng: float
    kind: CameraKind = CameraKind.FIXED
    limit_kmh: Optional[int] = None
    source: str = "OSM"
    active: bool = True
    end_lat: Optional[float] = None   # só SECTION
    end_lng: Optional[float] = None
    direction_deg: Optional[int] = None
    # Sentido nominal da rodovia ("N", "S", "L", "O"), para as fontes que dão só isso (SP).
    # Vira direction_deg com a geometria do OSM (common/sentido.py); não vai para o CSV.
    heading_hint: Optional[str] = field(default=None, compare=False)

    HEADER = ("lat", "lng", "kind", "limit_kmh", "source", "active", "end_lat", "end_lng", "direction_deg")

    def row(self) -> List[str]:
        return [
            f"{self.lat:.6f}", f"{self.lng:.6f}", self.kind.value,
            "" if self.limit_kmh is None else str(self.limit_kmh),
            self.source, "1" if self.active else "0",
            "" if self.end_lat is None else f"{self.end_lat:.6f}",
            "" if self.end_lng is None else f"{self.end_lng:.6f}",
            _dir(self.direction_deg),
        ]


@dataclass(frozen=True)
class Limit:
    """Ponto de limite. `estimated`: não é valor sinalizado, e sim estimado pela classe da via
    ou pela zona (ver common/infer.py); `low_kmh` é a estimativa pela tabela do lado baixo."""
    lat: float
    lng: float
    limit_kmh: int
    source: str = "OSM"
    estimated: bool = False
    low_kmh: Optional[int] = None
    direction_deg: Optional[int] = None

    HEADER = ("lat", "lng", "limit_kmh", "source", "estimated", "limit_low_kmh", "direction_deg")

    def row(self) -> List[str]:
        return [f"{self.lat:.6f}", f"{self.lng:.6f}", str(self.limit_kmh), self.source,
                "1" if self.estimated else "0", "" if self.low_kmh is None else str(self.low_kmh),
                _dir(self.direction_deg)]

    @staticmethod
    def from_row(r: Dict[str, str]) -> "Limit":
        low = r.get("limit_low_kmh") or ""
        return Limit(float(r["lat"]), float(r["lng"]), int(r["limit_kmh"]), r.get("source") or "OSM",
                     r.get("estimated") == "1", int(low) if low else None,
                     parse_dir(r.get("direction_deg")))


def _dir(d: Optional[int]) -> str:
    return "" if d is None else str(int(d) % 360)


def parse_dir(v: Optional[str]) -> Optional[int]:
    v = (v or "").strip()
    return int(float(v)) % 360 if v else None


def to_dir(bearing: Optional[float]) -> Optional[int]:
    """Rumo em graus (float) -> inteiro 0-359 para a coluna direction_deg."""
    return None if bearing is None else int(round(bearing)) % 360


@dataclass(frozen=True)
class Struct:
    """Trecho de ponte/viaduto ou túnel/subpassagem (aviso de estrutura na via)."""
    lat1: float
    lng1: float
    lat2: float
    lng2: float
    kind: str  # BRIDGE | TUNNEL

    HEADER = ("lat1", "lng1", "lat2", "lng2", "kind")

    def row(self) -> List[str]:
        return [f"{self.lat1:.6f}", f"{self.lng1:.6f}", f"{self.lat2:.6f}", f"{self.lng2:.6f}", self.kind]


_KIND_WEIGHT = {CameraKind.RED_LIGHT: 3, CameraKind.SECTION: 2, CameraKind.FIXED: 1}


def _key(lat: float, lng: float) -> Tuple[float, float]:
    return (round(lat, 5), round(lng, 5))


def merge_cameras(*groups: Iterable[Camera]) -> List[Camera]:
    """Colapsa radares a <= ~1 m entre si. Preferência: kind mais específico >
    tem limite > ativo. Ordem dos grupos desempata por último (passe os oficiais antes
    do OSM para o oficial ganhar). Sentido: o que alguma fonte souber vale; se duas fontes
    apontarem sentidos opostos no mesmo ponto, é um equipamento nos dois sentidos (vazio)."""
    from dataclasses import replace
    from datakit.common.geo import angle_diff

    best: Dict[Tuple[float, float], Camera] = {}
    dirs: Dict[Tuple[float, float], List[int]] = {}
    for g in groups:
        for c in g:
            k = _key(c.lat, c.lng)
            cur = best.get(k)
            if cur is None or _better_cam(c, cur):
                best[k] = c
            if c.direction_deg is not None:
                dirs.setdefault(k, []).append(c.direction_deg)
    out = []
    for k, c in best.items():
        ds = dirs.get(k, [])
        if ds and any(angle_diff(d, ds[0]) > 90 for d in ds):
            c = replace(c, direction_deg=None)
        elif ds and c.direction_deg is None:
            c = replace(c, direction_deg=ds[0])
        out.append(c)
    return sorted(out, key=lambda c: (c.lat, c.lng))


def _better_cam(new: Camera, cur: Camera) -> bool:
    nw = (_KIND_WEIGHT[new.kind], new.limit_kmh is not None, new.active)
    cw = (_KIND_WEIGHT[cur.kind], cur.limit_kmh is not None, cur.active)
    return nw > cw


def merge_limits(*groups: Iterable[Limit]) -> List[Limit]:
    """Último vence (grupos em ordem de prioridade), mas um valor sinalizado nunca é trocado
    por um estimado no mesmo ponto. Pontos de sentidos diferentes no mesmo lugar são
    pontos diferentes (placa de cada lado da pista)."""
    best: Dict[Tuple[float, float, Optional[int]], Limit] = {}
    for g in groups:
        for lim in g:
            k = (*_key(lim.lat, lim.lng), lim.direction_deg)
            cur = best.get(k)
            if cur is not None and not cur.estimated and lim.estimated:
                continue
            best[k] = lim
    return sorted(best.values(), key=lambda x: (x.lat, x.lng))


def override_limits(official: List[Limit], osm: List[Limit], radius_m: float = 50.0) -> List[Limit]:
    """Limites oficiais (placas ANTT, trechos da prefeitura) + os do OSM que não estiverem a
    menos de radius_m de nenhum oficial. Quem lê os pontos em sequência ao longo da via veria
    o valor alternar entre as duas fontes no mesmo trecho.

    Um oficial com sentido só cobre aquele sentido: o ponto do OSM sai quando há ali um oficial
    sem sentido, ou oficiais dos dois sentidos; com placa de um sentido só, o OSM fica para o
    outro."""
    from datakit.common.geo import angle_diff, haversine_m

    cell = radius_m / 111_000.0
    grid: Dict[Tuple[int, int], List[Limit]] = {}
    for o in official:
        grid.setdefault((int(o.lat // cell), int(o.lng // cell)), []).append(o)

    def near(p: Limit) -> bool:
        gy, gx = int(p.lat // cell), int(p.lng // cell)
        seen: List[int] = []
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                for o in grid.get((gy + dy, gx + dx), ()):
                    if haversine_m((p.lat, p.lng), (o.lat, o.lng)) <= radius_m:
                        if o.direction_deg is None:
                            return True
                        if any(angle_diff(o.direction_deg, d) > 90 for d in seen):
                            return True
                        seen.append(o.direction_deg)
        return False

    return list(official) + [p for p in osm if not near(p)]
