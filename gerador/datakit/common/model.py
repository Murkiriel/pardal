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
    # Vira direction_deg com a geometria do OSM (common/direction.py); não vai para o CSV.
    heading_hint: Optional[str] = field(default=None, compare=False)
    # ((sigla, número), km) do cadastro da própria fonte (DNIT, DER-SP), o mesmo do Inmetro: casa com o
    # medidor sem passar pela geometria do SNV, que erra o km em até ~2 km (inmetro_status.py), e vale
    # também em rodovia estadual, que o SNV não tem. Não vai para o CSV.
    road_km: Optional[Tuple[Tuple[str, int], float]] = field(default=None, compare=False)

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


@dataclass(frozen=True)
class Bump:
    """Lombada, quebra-molas, faixa elevada ou sonorizador (traffic_calming do OSM): o que faz a moto pular."""
    lat: float
    lng: float
    kind: str  # BUMP | HUMP | TABLE | CUSHION | RUMBLE_STRIP | UNSPECIFIED

    HEADER = ("lat", "lng", "kind")

    def row(self) -> List[str]:
        return [f"{self.lat:.6f}", f"{self.lng:.6f}", self.kind]


@dataclass(frozen=True)
class RadarStretch:
    """Trecho apto à fiscalização de velocidade com radar portátil (PRF, Res. Contran 798/2020): BR + km."""
    uf: str
    br: int
    km_from: float
    km_to: float


@dataclass(frozen=True)
class Toll:
    """Praça de pedágio (PLAZA, cabine) ou pórtico de free flow (FREE_FLOW, paga sem parar). Sem preço."""
    lat: float
    lng: float
    kind: str  # PLAZA | FREE_FLOW
    name: str
    source: str  # ANTT | OSM

    HEADER = ("lat", "lng", "kind", "name", "source")

    def row(self) -> List[str]:
        return [f"{self.lat:.6f}", f"{self.lng:.6f}", self.kind, self.name, self.source]


TOLL_SAME_M = 300.0  # a cabine do OSM a até isso de uma praça da ANTT é a mesma praça (pistas dos dois sentidos)


def merge_tolls(antt: List[Toll], osm: List[Toll]) -> List[Toll]:
    """As praças da ANTT, e as do OSM que não ficam a até TOLL_SAME_M de nenhuma delas; o OSM sozinho também se junta
    (as cabines de uma praça, uma por pista): uma por TOLL_SAME_M."""
    from datakit.common.geo import haversine_m
    kept: List[Toll] = list(antt)
    for t in osm:
        if all(haversine_m((t.lat, t.lng), (k.lat, k.lng)) > TOLL_SAME_M for k in kept):
            kept.append(t)
    return kept


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


def source_parts(source: str) -> List[str]:
    """'DNIT+OSM' -> ['DNIT', 'OSM']: a coluna source junta com '+' as fontes do mesmo radar."""
    return [p for p in (source or "").split("+") if p]


def join_sources(*sources: str) -> str:
    """Junta fontes sem repetir, oficiais antes do OSM: ('DNIT', 'OSM') -> 'DNIT+OSM'."""
    parts: List[str] = []
    for s in sources:
        for p in source_parts(s):
            if p not in parts:
                parts.append(p)
    return "+".join(sorted(parts, key=lambda p: (p == "OSM", parts.index(p))))


def _grid(cams: List[Camera], radius_m: float):
    """Índice dos radares por posição (item = posição na lista)."""
    from datakit.common.spatial import Grid
    return Grid(radius_m, ((c.lat, c.lng, i) for i, c in enumerate(cams)))


def _near(g, c: Camera):
    """Índices dos radares nas células que cobrem o raio em volta de c (quem chama mede)."""
    for _, _, i in g.candidates(c.lat, c.lng):
        yield i


def _fold(keep: Camera, other: Camera, source: str) -> Camera:
    """keep absorve other: herda o limite que falte e o fim do trecho (se other é de trecho)."""
    from dataclasses import replace
    section = other.kind == CameraKind.SECTION and keep.kind == CameraKind.FIXED and other.end_lat is not None
    return replace(
        keep,
        source=source,
        limit_kmh=keep.limit_kmh if keep.limit_kmh is not None else other.limit_kmh,
        kind=CameraKind.SECTION if section else keep.kind,
        end_lat=other.end_lat if section else keep.end_lat,
        end_lng=other.end_lng if section else keep.end_lng,
        direction_deg=keep.direction_deg if keep.direction_deg is not None else other.direction_deg,
        road_km=keep.road_km if keep.road_km is not None else other.road_km,
    )


# Dois órgãos publicando o mesmo radar (ex. Detran-DF e DNIT numa BR dentro do DF): a até 30 m e
# compatíveis, vira um só. Medido: 11 pares. Radares do mesmo órgão nunca se juntam (o órgão lista
# cada equipamento: um por pista, por faixa ou por aproximação de cruzamento).
def _audit_row(rule: str, a: Camera, b: Camera, dist: float) -> dict:
    """Uma junção, para a auditoria (data/audit/juncoes_<UF>.csv; não é publicada)."""
    return {"rule": rule, "source_a": a.source, "source_b": b.source, "dist_m": round(dist, 1),
            "kind_a": a.kind.value, "kind_b": b.kind.value,
            "limit_a": "" if a.limit_kmh is None else a.limit_kmh, "limit_b": "" if b.limit_kmh is None else b.limit_kmh,
            "direction_a": "" if a.direction_deg is None else a.direction_deg,
            "direction_b": "" if b.direction_deg is None else b.direction_deg,
            "active_a": int(a.active), "active_b": int(b.active), "lat": a.lat, "lng": a.lng}


AUDIT_HEADER = ("rule", "source_a", "source_b", "dist_m", "kind_a", "kind_b", "limit_a", "limit_b",
                "direction_a", "direction_b", "active_a", "active_b", "lat", "lng")


def merge_cross_agency(official: List[Camera], radius_m: float = 30.0,
                       audit: Optional[List[dict]] = None) -> Tuple[List[Camera], int]:
    """Fica a posição de quem tem sentido (senão, a do primeiro); fontes juntadas com '+';
    ativo se algum dos dois disser que está ativo (na dúvida, avisa)."""
    from dataclasses import replace
    from datakit.common.geo import haversine_m

    cams = list(official)
    g = _grid(cams, radius_m)
    gone = set()
    n = 0
    for i, c in enumerate(cams):
        if i in gone:
            continue
        for j in _near(g, c):
            o = cams[j]
            if j <= i or j in gone or set(source_parts(c.source)) & set(source_parts(o.source)):
                continue
            dist = haversine_m((c.lat, c.lng), (o.lat, o.lng))
            if not _absorbs(c, o) or dist > radius_m:
                continue
            if audit is not None:
                audit.append(_audit_row("cross_agency", c, o, dist))
            keep, other = (o, c) if (c.direction_deg is None and o.direction_deg is not None) else (c, o)
            c = replace(_fold(keep, other, join_sources(c.source, o.source)), active=c.active or o.active)
            cams[i] = c
            gone.add(j)
            n += 1
    return [c for k, c in enumerate(cams) if k not in gone], n


# Dois radares do OSM a até isto são o mesmo ponto de fiscalização (faixas lado a lado do mesmo
# pórtico, ou o mesmo radar marcado duas vezes). Medido: 278 pares a < 8 m; de 8 a 30 m (~1.550)
# são quase sempre um equipamento por pista ou por aproximação de cruzamento, e ficam.
OSM_SAME_POINT_M = 8.0


def collapse_osm(osm: List[Camera], radius_m: float = OSM_SAME_POINT_M,
                 audit: Optional[List[dict]] = None) -> Tuple[List[Camera], int]:
    """Junta radares do OSM a até radius_m (avanço de sinal nunca com radar de velocidade); fica o
    de tipo mais específico/com limite (mesma preferência de merge_cameras)."""
    from datakit.common.geo import haversine_m

    cams = list(osm)
    g = _grid(cams, radius_m)
    gone = set()
    n = 0
    for i, c in enumerate(cams):
        if i in gone:
            continue
        for j in _near(g, c):
            o = cams[j]
            if j <= i or j in gone or not _absorbs(c, o):
                continue
            dist = haversine_m((c.lat, c.lng), (o.lat, o.lng))
            if dist > radius_m:
                continue
            if audit is not None:
                audit.append(_audit_row("osm_osm", c, o, dist))
            keep, other = (o, c) if _better_cam(o, c) else (c, o)
            c = _fold(keep, other, c.source)
            cams[i] = c
            gone.add(j)
            n += 1
    return [c for k, c in enumerate(cams) if k not in gone], n


# Radar do OSM a até isto de um oficial compatível é o mesmo equipamento mapeado de novo (as
# coordenadas das fontes oficiais e do OSM costumam diferir em 5-20 m). Medido: 2.502 dos 10.420
# radares do OSM ficavam a <= 30 m de um oficial e saíam em dobro nos arquivos (dois alertas no GPS).
ABSORB_M = 30.0


def _absorbs(off: Camera, osm: Camera) -> bool:
    """O oficial pode ser o mesmo equipamento do radar do OSM? Avanço de sinal só com avanço de
    sinal (ao lado de um radar de velocidade é outro equipamento); sentidos conhecidos opostos, não."""
    from datakit.common.geo import angle_diff

    if (off.kind == CameraKind.RED_LIGHT) != (osm.kind == CameraKind.RED_LIGHT):
        return False
    if off.direction_deg is not None and osm.direction_deg is not None:
        return angle_diff(off.direction_deg, osm.direction_deg) <= 90
    return True


def absorb_osm(official: List[Camera], osm: List[Camera], radius_m: float = ABSORB_M,
               audit: Optional[List[dict]] = None) -> Tuple[List[Camera], List[Camera], int]:
    """Junta cada radar do OSM ao oficial compatível mais perto (a até radius_m, ativo antes de
    inativo). Fica o oficial: posição, sentido e situação (medido: a posição oficial acerta mais o
    lado da pista, ver FONTES.md); do OSM ele herda o limite quando não tem e o fim do trecho
    quando o OSM sabe que é radar de trecho, e a fonte vira '<oficial>+OSM' (confirmado por duas
    fontes). Oficiais não se juntam aqui (ver merge_cross_agency). Devolve (oficiais, OSM que
    sobraram, quantos juntados)."""
    from dataclasses import replace
    from datakit.common.geo import haversine_m

    official = list(official)
    grid = _grid(official, radius_m)
    rest: List[Camera] = []
    joined = 0
    for c in osm:
        best = None
        for i in _near(grid, c):
            o = official[i]
            if not _absorbs(o, c):
                continue
            d = haversine_m((c.lat, c.lng), (o.lat, o.lng))
            if d <= radius_m and (best is None or (not o.active, d) < best[0]):
                best = ((not o.active, d), i)
        if best is None:
            rest.append(c)
            continue
        i = best[1]
        o = official[i]
        if audit is not None:
            audit.append(_audit_row("osm_official", o, c, best[0][1]))
        official[i] = replace(_fold(o, replace(c, direction_deg=None), join_sources(o.source, "OSM")),
                              direction_deg=o.direction_deg)
        joined += 1
    return official, rest, joined


def deactivate_near(cams: List[Camera], dead: List[Tuple[float, float]], alive: List[Camera],
                    dead_m: float = 20.0, alive_m: float = ABSORB_M) -> Tuple[List[Camera], int]:
    """Marca inativo o radar que está num local que o órgão desativou (a até dead_m) e não tem
    nenhum radar oficial ativo por perto (a até alive_m): o mapeamento ficou velho."""
    from dataclasses import replace
    from datakit.common.spatial import Grid

    if not dead:
        return cams, 0

    gd = Grid(dead_m, ((lat, lng, None) for lat, lng in dead))
    ga = Grid(alive_m, ((a.lat, a.lng, None) for a in alive if a.active))
    out, n = [], 0
    for c in cams:
        if c.active and any(gd.within(c.lat, c.lng)) and not any(ga.within(c.lat, c.lng)):
            c = replace(c, active=False)
            n += 1
        out.append(c)
    return out, n


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
    from datakit.common.geo import angle_diff
    from datakit.common.spatial import Grid

    grid = Grid(radius_m, ((o.lat, o.lng, o) for o in official))

    def near(p: Limit) -> bool:
        seen: List[int] = []
        for _, o in grid.within(p.lat, p.lng):
            if o.direction_deg is None:
                return True
            if any(angle_diff(o.direction_deg, d) > 90 for d in seen):
                return True
            seen.append(o.direction_deg)
        return False

    return list(official) + [p for p in osm if not near(p)]
