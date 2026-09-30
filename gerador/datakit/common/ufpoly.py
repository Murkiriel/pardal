"""Polígono real de cada UF (malha estadual do IBGE), para recorte exato — opcional.

Precisa de `shapely` e da malha das UFs da API de malhas do IBGE (v3, qualidade máxima, GeoJSON
com a UF pelo código IBGE em `codarea`), guardada em data/raw/. Sem qualquer um dos dois,
`polygons()` devolve {} e o build cai no recorte por bbox.

Antes vinha de um espelho no GitHub (geodata-br-states). Comparada em 2026-09-30 (FONTES.md), a
oficial tem 52 mil vértices contra 137 mil (1 MB contra 5,6 MB), não sobrepõe UFs vizinhas (a
antiga sobrepunha PE e PB) e põe mais radares de órgão estadual/municipal na UF do órgão.
"""
from __future__ import annotations

import json
import os
from typing import Dict, Optional

MESH_URL = ("https://servicodados.ibge.gov.br/api/v3/malhas/paises/BR"
            "?formato=application/vnd.geo%2Bjson&intrarregiao=UF&qualidade=maxima")
MESH_FILE = "ibge_malha_uf.json"
# Código IBGE da UF (codarea) -> sigla.
IBGE_UF = {"11": "RO", "12": "AC", "13": "AM", "14": "RR", "15": "PA", "16": "AP", "17": "TO", "21": "MA",
           "22": "PI", "23": "CE", "24": "RN", "25": "PB", "26": "PE", "27": "AL", "28": "SE", "29": "BA",
           "31": "MG", "32": "ES", "33": "RJ", "35": "SP", "41": "PR", "42": "SC", "43": "RS", "50": "MS",
           "51": "MT", "52": "GO", "53": "DF"}


def _ensure_mesh(raw_dir: str) -> Optional[str]:
    path = os.path.join(raw_dir, MESH_FILE)
    if not os.path.exists(path):
        try:
            os.makedirs(raw_dir, exist_ok=True)
            from datakit.sources._http import get_bytes
            data = get_bytes(MESH_URL)
            with open(path, "wb") as f:
                f.write(data)
        except Exception as e:  # noqa: BLE001
            from datakit import failures
            failures.record("IBGE (malha das UFs; recorte por bbox)", e)
            return None
    return path


def polygons(raw_dir: str) -> Dict[str, object]:
    """{UF: shapely geometry}. {} se shapely ou a malha não estiverem disponíveis. Lê a cada
    chamada: o build guarda o resultado no contexto (BuildContext.polygons)."""
    try:
        from shapely.geometry import shape
    except Exception:  # noqa: BLE001
        print("[ufpoly] shapely não instalado; recorte por bbox")
        return {}
    path = _ensure_mesh(raw_dir)
    if path is None:
        return {}
    with open(path, encoding="utf-8") as f:
        gj = json.load(f)
    out: Dict[str, object] = {}
    for ft in gj.get("features", []):
        props = ft.get("properties", {})
        uf = IBGE_UF.get(str(props.get("codarea"))) or props.get("SIGLA") or props.get("sigla")
        if not uf:
            continue
        try:
            out[uf.upper()] = shape(ft["geometry"]).buffer(0)  # buffer(0) conserta anéis
        except Exception:  # noqa: BLE001
            continue
    print(f"[ufpoly] {len(out)} polígonos de UF carregados")
    return out
