"""Situação dos radares pela aferição do Inmetro (rodovias federais).

Para cada radar FIXED a menos de 60 m de uma BR (SNV Rotas), toma o km dele sobre a rota e
procura medidores fixos do Inmetro na mesma BR/UF com km a até MATCH_KM:
  * algum medidor válido perto            -> ativo (confirmado)
  * só medidores vencidos/reprovados perto e nenhum válido a até GUARD_KM -> inativo
  * nenhum medidor perto                  -> fica como veio da fonte
Trechos de concessão federal ficam de fora: ali o km cadastrado é o da concessão, não o do
SNV (ver common/lrs.py). O km do SNV cai a ~230 m da coordenada real na mediana, por isso a
janela larga e a trava de "nenhum válido por perto" antes de desligar um radar.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import replace
from typing import Dict, List, Tuple

from datakit.common.lrs import SnvRoutes, in_ranges
from datakit.common.model import Camera, CameraKind, source_parts
from datakit.sources.inmetro import Meter

# Só fontes sem situação própria: ANTT e DER-SP/Artesp já dizem se o radar está ativo.
STATUS_FROM_INMETRO = {"OSM", "DNIT", "DER-GO", "DETRAN-DF"}

ON_ROUTE_M = 60.0
MATCH_KM = 1.0
GUARD_KM = 3.0


def eligible(source: str) -> bool:
    """A situação do radar pode vir do Inmetro? Só se nenhuma das fontes oficiais dele tiver
    situação própria (radar juntado 'ANTT+OSM' fica com a situação da ANTT); só do OSM, sim."""
    official = [p for p in source_parts(source) if p != "OSM"]
    if not official:
        return "OSM" in STATUS_FROM_INMETRO
    return all(p in STATUS_FROM_INMETRO for p in official)


def index_meters(meters: List[Meter]) -> Dict[Tuple[int, str], List[Tuple[float, bool]]]:
    """(BR, UF) -> [(km, válido)] dos medidores fixos com rodovia federal + km."""
    idx: Dict[Tuple[int, str], List[Tuple[float, bool]]] = defaultdict(list)
    for m in meters:
        if m.fixed and m.road and m.road[0] == "BR" and m.km is not None:
            idx[(m.road[1], m.uf)].append((m.km, m.valid))
    return dict(idx)


def decide(near: List[Tuple[float, bool]], guard: List[Tuple[float, bool]]):
    """None = sem opinião; True/False = ativo/inativo. `near` = medidores a até MATCH_KM,
    `guard` = a até GUARD_KM. Puro, testado."""
    if any(v for _, v in near):
        return True
    if near and not any(v for _, v in guard):
        return False
    return None


def apply(cams: List[Camera], uf: str, meters_idx, snv: SnvRoutes, concessions) -> Tuple[List[Camera], Dict[str, int]]:
    stats = {"confirmados": 0, "desativados": 0, "sem_medidor": 0}
    out: List[Camera] = []
    for c in cams:
        if c.kind != CameraKind.FIXED or not eligible(c.source):
            out.append(c)
            continue
        hit = snv.nearest_any(uf, (c.lat, c.lng), ON_ROUTE_M)
        if hit is None:
            out.append(c)
            continue
        br, _, km, _ = hit
        if in_ranges(concessions.get((br, uf), []), km):
            out.append(c)
            continue
        ms = meters_idx.get((br, uf), [])
        near = [m for m in ms if abs(m[0] - km) <= MATCH_KM]
        guard = [m for m in ms if abs(m[0] - km) <= GUARD_KM]
        verdict = decide(near, guard)
        if verdict is None:
            stats["sem_medidor"] += 1
            out.append(c)
        elif verdict:
            stats["confirmados"] += 1
            out.append(c if c.active else replace(c, active=True))
        else:
            stats["desativados"] += 1
            out.append(replace(c, active=False))
    return out, stats
