#!/usr/bin/env python3
"""Converte um arquivo CSV do Pardal para GeoJSON.

Uso:
    python scripts/to_geojson.py estados/GO/limites.csv
    python scripts/to_geojson.py estados/GO/limites.csv --no-estimated
    python scripts/to_geojson.py brasil/limites_estimados.csv.gz -o estimados.geojson

Funciona com radares, limites, limites estimados e estruturas, em .csv ou .csv.gz. O tipo é
reconhecido pelas colunas: `lat,lng` vira ponto; `lat1,lng1,lat2,lng2` (estruturas) vira
linha. As demais colunas viram propriedades, com números como números e `active` /
`estimated` como verdadeiro/falso.

Só precisa de Python 3.8 ou mais novo, sem instalar nada. Por padrão grava ao lado do
arquivo de entrada, com a extensão trocada para .geojson.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import io
import json
import os
import sys
from typing import Dict, Iterable, Iterator, Optional

BOOLEAN = {"active", "estimated"}
ATTRIBUTION = "Pardal — © colaboradores do OpenStreetMap (ODbL 1.0) e fontes listadas no README"


def _open(path: str) -> io.TextIOBase:
    if path.endswith(".gz"):
        return io.TextIOWrapper(gzip.open(path, "rb"), encoding="utf-8", newline="")
    return open(path, encoding="utf-8", newline="")


def _value(key: str, raw: str):
    if raw == "":
        return None
    if key in BOOLEAN:
        return raw == "1"
    try:
        return int(raw)
    except ValueError:
        try:
            return float(raw)
        except ValueError:
            return raw


def feature(row: Dict[str, str]) -> Optional[dict]:
    """Uma linha do CSV -> Feature GeoJSON (ou None se faltar coordenada)."""
    try:
        if "lat1" in row:
            coords = [[float(row["lng1"]), float(row["lat1"])], [float(row["lng2"]), float(row["lat2"])]]
            geometry = {"type": "LineString", "coordinates": coords}
            skip = {"lat1", "lng1", "lat2", "lng2"}
        else:
            geometry = {"type": "Point", "coordinates": [float(row["lng"]), float(row["lat"])]}
            skip = {"lat", "lng"}
    except (KeyError, ValueError, TypeError):
        return None
    props = {k: _value(k, v or "") for k, v in row.items() if k not in skip and k is not None}
    return {"type": "Feature", "geometry": geometry, "properties": props}


def features(rows: Iterable[Dict[str, str]], no_estimated: bool = False) -> Iterator[dict]:
    for row in rows:
        if no_estimated and row.get("estimated") == "1":
            continue
        f = feature(row)
        if f is not None:
            yield f


def converter(input_path: str, output: str, no_estimated: bool = False) -> int:
    """Grava o GeoJSON em saida, um Feature por vez (não carrega o arquivo inteiro na
    memória). Devolve quantos Features foram escritos."""
    n = 0
    with _open(input_path) as fi, open(output, "w", encoding="utf-8") as fo:
        fo.write('{"type":"FeatureCollection","attribution":' + json.dumps(ATTRIBUTION, ensure_ascii=False)
                 + ',"features":[\n')
        for f in features(csv.DictReader(fi), no_estimated):
            fo.write((",\n" if n else "") + json.dumps(f, ensure_ascii=False, separators=(",", ":")))
            n += 1
        fo.write("\n]}\n")
    return n


def default_output(input_path: str) -> str:
    base = input_path[:-3] if input_path.endswith(".gz") else input_path
    return (base[:-4] if base.endswith(".csv") else base) + ".geojson"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Converte um CSV do Pardal (radares, limites, estruturas) para GeoJSON.")
    ap.add_argument("input_path", help="arquivo .csv ou .csv.gz do Pardal")
    ap.add_argument("-o", "--output", help="arquivo .geojson de saída (padrão: ao lado da entrada)")
    ap.add_argument("--no-estimated", action="store_true",
                    help="deixa de fora os limites estimados (estimated=1), só os sinalizados")
    args = ap.parse_args(argv)
    if not os.path.exists(args.input_path):
        ap.error(f"arquivo não encontrado: {args.input_path}")
    output = args.output or default_output(args.input_path)
    n = converter(args.input_path, output, args.no_estimated)
    print(f"{n} itens -> {output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
