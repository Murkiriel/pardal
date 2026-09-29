"""Radares em formatos de mapa e de GPS, a partir dos CSVs de data/dist/.

    python -m datakit.build_formats            # depois de datakit.build --all

Para cada estado e para o Brasil (radares_<X>.csv):
  radares_<X>.geojson — todos os radares, com todas as colunas (inativos com active=false)
  radares_<X>.kml     — só os ativos, uma pasta por tipo (Google Earth / My Maps)
  radares_<X>.gpx     — só os ativos, como waypoints (GPS, OsmAnd, apps de navegação)

Limites e estruturas ficam só em CSV: em GeoJSON os limites dos estados somavam 451 MB (70%
do repositório) para os mesmos pontos do CSV. Quem quiser GeoJSON converte com o
scripts/para_geojson.py do repositório. Em GPX/KML nenhum limite: GPS não usa ponto de
limite como ponto de interesse.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from typing import Dict, List
from xml.sax.saxutils import escape

KIND_LABEL = {"FIXED": "Radar", "SECTION": "Radar de trecho", "RED_LIGHT": "Avanço de sinal"}
KIND_FOLDER = {"FIXED": "Radares de velocidade", "SECTION": "Radares de trecho",
               "RED_LIGHT": "Avanço de sinal vermelho"}
ATTRIBUTION = "Pardal — © colaboradores do OpenStreetMap (ODbL 1.0) e fontes abertas listadas no README"


def read(path: str) -> List[Dict[str, str]]:
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def name(r: Dict[str, str]) -> str:
    label = KIND_LABEL.get(r["kind"], "Radar")
    return f"{label} {r['limit_kmh']} km/h" if r.get("limit_kmh") else label


_ROSE = ("norte", "nordeste", "leste", "sudeste", "sul", "sudoeste", "oeste", "noroeste")


def sentido(r: Dict[str, str]) -> str:
    """'sentido 92° (leste)' para a descrição; vazio quando vale para os dois sentidos."""
    v = (r.get("direction_deg") or "").strip()
    if not v:
        return ""
    deg = int(float(v)) % 360
    return f"sentido {deg}° ({_ROSE[int((deg + 22.5) // 45) % 8]})"


def description(r: Dict[str, str]) -> str:
    return " · ".join(x for x in (f"Fonte: {r.get('source', '')}", sentido(r)) if x)


def active(r: Dict[str, str]) -> bool:
    return r.get("active", "1") != "0"


def _point(r: Dict[str, str]) -> dict:
    return {"type": "Point", "coordinates": [float(r["lng"]), float(r["lat"])]}


def to_geojson(rows: List[Dict[str, str]]) -> dict:
    feats = []
    for r in rows:
        props = {
            "kind": r["kind"],
            "limit_kmh": int(r["limit_kmh"]) if r.get("limit_kmh") else None,
            "source": r.get("source") or None,
            "active": active(r),
        }
        if r.get("end_lat") and r.get("end_lng"):
            props["end"] = [float(r["end_lng"]), float(r["end_lat"])]
        props["direction_deg"] = int(r["direction_deg"]) if r.get("direction_deg") else None
        feats.append({"type": "Feature", "geometry": _point(r), "properties": props})
    return {"type": "FeatureCollection", "attribution": ATTRIBUTION, "features": feats}


def to_kml(rows: List[Dict[str, str]], title: str = "Brasil") -> str:
    by_kind: Dict[str, List[Dict[str, str]]] = {}
    for r in rows:
        if active(r):
            by_kind.setdefault(r["kind"], []).append(r)
    out = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<kml xmlns="http://www.opengis.net/kml/2.2"><Document>',
           f"<name>{escape(f'Pardal — radares — {title}')}</name>",
           f"<description>{escape(ATTRIBUTION)}</description>"]
    for kind in ("FIXED", "SECTION", "RED_LIGHT"):
        items = by_kind.get(kind, [])
        if not items:
            continue
        out.append(f"<Folder><name>{escape(KIND_FOLDER[kind])}</name>")
        for r in items:
            desc = description(r)
            out.append(f"<Placemark><name>{escape(name(r))}</name><description>{escape(desc)}</description>"
                       f"<Point><coordinates>{r['lng']},{r['lat']},0</coordinates></Point></Placemark>")
        out.append("</Folder>")
    out.append("</Document></kml>")
    return "\n".join(out)


def to_gpx(rows: List[Dict[str, str]], title: str = "Brasil") -> str:
    out = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<gpx version="1.1" creator="Pardal" xmlns="http://www.topografix.com/GPX/1/1">',
           f"<metadata><name>{escape(f'Pardal — radares — {title}')}</name><desc>{escape(ATTRIBUTION)}</desc></metadata>"]
    for r in rows:
        if not active(r):
            continue
        out.append(f'<wpt lat="{r["lat"]}" lon="{r["lng"]}"><name>{escape(name(r))}</name>'
                   f"<desc>{escape(description(r))}</desc><type>{escape(r['kind'])}</type></wpt>")
    out.append("</gpx>")
    return "\n".join(out)


def _write_json(path: str, obj: dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))


def _write_text(path: str, text: str) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def build(dist_dir: str) -> Dict[str, int]:
    stats = {"radares": 0}
    for fname in sorted(os.listdir(dist_dir)):
        m = re.fullmatch(r"radares_([A-Z]{2})\.csv", fname)
        if not m:
            continue
        area = m.group(1)
        rows = read(os.path.join(dist_dir, fname))
        base = os.path.join(dist_dir, f"radares_{area}")
        title = "Brasil" if area == "BR" else area
        _write_json(base + ".geojson", to_geojson(rows))
        _write_text(base + ".kml", to_kml(rows, title))
        _write_text(base + ".gpx", to_gpx(rows, title))
        stats["radares"] += 1
    return stats


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="datakit.build_formats")
    ap.add_argument("--dist", default="data/dist")
    args = ap.parse_args(argv)
    print(f"[formats] {build(args.dist)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
