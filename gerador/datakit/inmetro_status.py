"""Situação dos radares pela aferição do Inmetro (rodovias federais).

Para cada radar FIXED a menos de 60 m de uma BR (SNV Rotas), toma o km dele sobre a rota e
procura medidores fixos do Inmetro na mesma BR/UF com km a até MATCH_KM:
  * algum medidor válido perto            -> ativo (confirmado)
  * só medidores vencidos/reprovados perto e nenhum válido a até GUARD_KM -> inativo
  * nenhum medidor perto                  -> fica como veio da fonte
Radar que traz o km do próprio cadastro (road_km, o DNIT) casa por ele, que é o mesmo do Inmetro:
em Itaberaí (BR-070) o SNV erra o km em 1,9 km. Os outros, pelo km do SNV.
Radar sem limite (o PNCV do DNIT não traz nenhum) confirmado por medidor válido ganha a velocidade
nominal dele: o medidor que confirma é o mesmo equipamento. Vale o do km mais perto; dois no mesmo
km (um por sentido), o menor.
Trechos de concessão federal ficam de fora: ali o km cadastrado é o da concessão, não o do
SNV (ver common/lrs.py). O km do SNV cai a ~230 m da coordenada real na mediana, por isso a
janela larga e a trava de "nenhum válido por perto" antes de desligar um radar.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import replace
from typing import Dict, List, Optional, Tuple

from datakit.common.lrs import SnvRoutes, in_ranges
from datakit.common.model import Camera, CameraKind, source_parts
from datakit.sources.inmetro import Meter

# Só fontes sem situação própria: ANTT e DER-SP/Artesp já dizem se o radar está ativo.
STATUS_FROM_INMETRO = {"OSM", "DNIT", "DER-GO", "DETRAN-DF"}

ON_ROUTE_M = 60.0
MATCH_KM = 1.0
GUARD_KM = 3.0
# Medidores a até isto além do mais perto contam como "no mesmo km" para o limite.
SAME_KM = 0.05

MeterAt = Tuple[float, bool, Optional[int]]   # (km, válido, velocidade nominal)


def eligible(source: str) -> bool:
    """A situação do radar pode vir do Inmetro? Só se nenhuma das fontes oficiais dele tiver
    situação própria (radar juntado 'ANTT+OSM' fica com a situação da ANTT); só do OSM, sim."""
    official = [p for p in source_parts(source) if p != "OSM"]
    if not official:
        return "OSM" in STATUS_FROM_INMETRO
    return all(p in STATUS_FROM_INMETRO for p in official)


def index_meters(meters: List[Meter]) -> Dict[Tuple[int, str], List[MeterAt]]:
    """(BR, UF) -> [(km, válido, limite)] dos medidores fixos com rodovia federal + km."""
    idx: Dict[Tuple[int, str], List[MeterAt]] = defaultdict(list)
    for m in meters:
        if m.fixed and m.road and m.road[0] == "BR" and m.km is not None:
            idx[(m.road[1], m.uf)].append((m.km, m.valid, m.limit_kmh))
    return dict(idx)


def decide(near: List[MeterAt], guard: List[MeterAt]):
    """None = sem opinião; True/False = ativo/inativo. `near` = medidores a até MATCH_KM,
    `guard` = a até GUARD_KM. Puro, testado."""
    if any(m[1] for m in near):
        return True
    if near and not any(m[1] for m in guard):
        return False
    return None


def meter_limit(near: List[MeterAt], km: float) -> Optional[int]:
    """Velocidade nominal do medidor válido de km mais perto; empate (mesmo km), a menor."""
    valid = [(abs(m[0] - km), m[2]) for m in near if m[1] and m[2] is not None]
    if not valid:
        return None
    closest = min(d for d, _ in valid)
    return min(v for d, v in valid if d <= closest + SAME_KM)


def _locate(c: Camera, uf: str, snv: SnvRoutes, concessions) -> Optional[Tuple[int, float]]:
    """(BR, km) do radar para casar com o Inmetro: o da fonte, se ela dá; senão o do SNV, fora de
    trecho concedido (ali o km do Inmetro é o da concessão). None = não dá para casar."""
    if c.road_km is not None:
        return c.road_km
    hit = snv.nearest_any(uf, (c.lat, c.lng), ON_ROUTE_M)
    if hit is None:
        return None
    br, _, km, _ = hit
    return None if in_ranges(concessions.get((br, uf), []), km) else (br, km)


def apply(cams: List[Camera], uf: str, meters_idx, snv: SnvRoutes, concessions) -> Tuple[List[Camera], Dict[str, int]]:
    stats = {"confirmed": 0, "deactivated": 0, "no_meter": 0, "limits_filled": 0}
    out: List[Camera] = []
    for c in cams:
        if c.kind != CameraKind.FIXED or not eligible(c.source):
            out.append(c)
            continue
        located = _locate(c, uf, snv, concessions)
        if located is None:
            out.append(c)
            continue
        br, km = located
        ms = meters_idx.get((br, uf), [])
        near = [m for m in ms if abs(m[0] - km) <= MATCH_KM]
        guard = [m for m in ms if abs(m[0] - km) <= GUARD_KM]
        verdict = decide(near, guard)
        if verdict is None:
            stats["no_meter"] += 1
            out.append(c)
        elif verdict:
            stats["confirmed"] += 1
            limit = c.limit_kmh if c.limit_kmh is not None else meter_limit(near, km)
            stats["limits_filled"] += limit != c.limit_kmh
            out.append(replace(c, active=True, limit_kmh=limit))
        else:
            stats["deactivated"] += 1
            out.append(replace(c, active=False))
    return out, stats
