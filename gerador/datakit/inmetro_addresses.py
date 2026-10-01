"""Radares pelos endereços do Inmetro, com a coordenada tirada do cadastro de endereços do IBGE.

O Inmetro lista todo medidor de velocidade aferido do país, com a velocidade de cada faixa e a
validade da aferição, mas sem coordenada. Onde o local é "rodovia + km", só serve para a situação
(inmetro_status.py). Onde é endereço ("rua + número" ou cruzamento de duas ruas), o CNEFE do IBGE
(sources/cnefe.py) dá o ponto: medido nas cinco capitais com lista oficial, mediana de 23 m até o
radar publicado mais perto e 89% a até 100 m (FONTES.md).

A posição é pior que a das fontes com coordenada própria e que a do OSM, então o medidor nunca
move um radar conhecido: a até JOIN_M de um, só o confirma (a fonte ganha "+INMETRO", o limite é
preenchido se faltava). Só vira radar novo onde não há nenhum por perto.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Dict, Iterable, List, Optional, Tuple

from datakit import failures
from datakit.common.address import parse_crossing, parse_number
from datakit.common.model import Camera, CameraKind, join_sources, source_parts
from datakit.common.spatial import Grid
from datakit.inmetro_status import eligible
from datakit.sources import cnefe
from datakit.sources.inmetro import Meter

SOURCE = "INMETRO"
# O ponto do cadastro fica a até 100 m do radar em 89% dos casos medidos, e só 6% ficam entre 100 e
# 300 m: um radar conhecido a até 100 m é o mesmo equipamento. Criar outro ali daria dois avisos seguidos.
JOIN_M = 100.0
# Dois locais do Inmetro que caem a até isto são o mesmo lugar escrito de dois jeitos ("Nº 1320" e
# "OPOSTO AO Nº 1320"): um radar só.
SAME_PLACE_M = 30.0
# Os medidores de Brasília vêm no arquivo de Goiás; o cadastro de endereços deles fica na pasta do DF.
_CNEFE_UF = {("GO", "BRASILIA"): "DF"}


def places(meters: Iterable[Meter]) -> Dict[str, Dict[str, Optional[int]]]:
    """{município: {local: menor limite entre os medidores do local}} dos medidores fixos, com
    aferição válida e sem rodovia + km (os que só têm endereço)."""
    out: Dict[str, Dict[str, Optional[int]]] = {}
    for m in meters:
        if not (m.fixed and m.valid) or m.road:
            continue
        local = " ".join(m.local.replace('"', "").split())
        locals_ = out.setdefault(m.municipality, {})
        known = locals_.get(local)
        speeds = [v for v in (known, m.limit_kmh) if v is not None]
        locals_[local] = min(speeds) if speeds else None
    return out


def _resolve(local: str, streets: Dict[str, List[cnefe.Point]]) -> Optional[Tuple[float, float]]:
    """O ponto do local: pelo número de porta; sem ele (ou sem a rua no cadastro), pelo cruzamento."""
    numbered = parse_number(local)
    if numbered:
        street = cnefe.match_street(numbered[0], streets)
        point = cnefe.locate_number(streets[street], numbered[1]) if street else None
        if point:
            return point
    crossing = parse_crossing(local)
    if crossing:
        a, b = (cnefe.match_street(k, streets) for k in crossing)
        if a and b and a != b:
            return cnefe.crossing_point(streets[a], streets[b])
    return None


def _wanted(locals_: Iterable[str]) -> List[str]:
    """Chaves das ruas que os locais citam (as que vale a pena ler do cadastro)."""
    keys: List[str] = []
    for local in locals_:
        numbered = parse_number(local)
        if numbered:
            keys.append(numbered[0])
        keys.extend(parse_crossing(local) or ())
    return keys


def geocode(meters: Iterable[Meter], uf: str, raw_dir: str) -> List[Camera]:
    """Um radar (fonte INMETRO, ativo, com o limite do medidor) por local do arquivo do Inmetro
    desta UF que o cadastro do IBGE consegue localizar. Município cujo arquivo não baixa é falha
    de fonte; município sem arquivo com esse nome é aviso."""
    by_city = places(meters)
    listings: Dict[str, Dict[str, str]] = {}
    out: List[Camera] = []
    n_places = n_addressable = 0
    for city, locals_ in sorted(by_city.items()):
        n_places += len(locals_)
        wanted = _wanted(locals_)
        if not wanted:
            continue
        cnefe_uf = _CNEFE_UF.get((uf, cnefe._name_key(city)), uf)
        try:
            if cnefe_uf not in listings:
                listings[cnefe_uf] = cnefe.municipality_files(cnefe_uf)
            href = cnefe.file_for(city, listings[cnefe_uf])
            if href is None:
                failures.warn(f"CNEFE {uf}", f"sem arquivo de endereços para o município {city}")
                continue
            streets = cnefe.read_streets(cnefe.ensure(cnefe_uf, href, raw_dir), wanted)
        except Exception as e:  # noqa: BLE001
            failures.record(f"CNEFE {city}/{uf}", e)
            continue
        for local, limit in sorted(locals_.items()):
            if not (parse_number(local) or parse_crossing(local)):
                continue
            n_addressable += 1
            point = _resolve(local, streets)
            if point:
                out.append(Camera(point[0], point[1], CameraKind.FIXED, limit, SOURCE, True))
    print(f"[build] Inmetro {uf}, endereços: {n_places} locais, {n_addressable} com rua + número ou "
          f"cruzamento, {len(out)} localizados pelo CNEFE")
    return out


def apply(cams: List[Camera], points: List[Camera]) -> Tuple[List[Camera], Dict[str, int]]:
    """Junta os locais do Inmetro aos radares: perto de um radar de velocidade (até JOIN_M; ativo
    antes de inativo), confirma-o; longe de todos, vira radar novo. Radar inativo de fonte sem
    situação própria (OSM, por exemplo) volta a ativo: há medidor com aferição válida ali."""
    stats = {"confirmed": 0, "limits_filled": 0, "reactivated": 0, "new": 0}
    cams = list(cams)
    grid: Grid[int] = Grid(JOIN_M, ((c.lat, c.lng, i) for i, c in enumerate(cams)))
    new: List[Camera] = []
    new_grid: Grid[int] = Grid(SAME_PLACE_M)
    for p in points:
        near = [(not cams[i].active, d, i) for d, i in grid.within(p.lat, p.lng)
                if cams[i].kind != CameraKind.RED_LIGHT]
        if near:
            i = min(near)[2]
            c = cams[i]
            if SOURCE not in source_parts(c.source):
                stats["confirmed"] += 1
            if c.limit_kmh is None and p.limit_kmh is not None:
                stats["limits_filled"] += 1
            revive = not c.active and eligible(c.source)
            stats["reactivated"] += revive
            cams[i] = replace(c, source=join_sources(c.source, SOURCE), active=c.active or revive,
                              limit_kmh=c.limit_kmh if c.limit_kmh is not None else p.limit_kmh)
            continue
        same = sorted(new_grid.within(p.lat, p.lng))
        if same:
            j = same[0][1]
            speeds = [v for v in (new[j].limit_kmh, p.limit_kmh) if v is not None]
            new[j] = replace(new[j], limit_kmh=min(speeds) if speeds else None)
        else:
            new_grid.add(p.lat, p.lng, len(new))
            new.append(p)
    stats["new"] = len(new)
    return cams + new, stats
