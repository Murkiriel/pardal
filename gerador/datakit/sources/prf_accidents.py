"""PRF — acidentes com moto nas rodovias federais (dados abertos da PRF, "Documento CSV de Acidentes <ano> (Agrupados
por pessoa)").

A página de dados abertos da PRF lista, por ano, os acidentes que ela atende em três cortes, um zip no Google Drive cada
(atualizado todo mês). O "agrupados por pessoa" tem um envolvido por linha, com o acidente (id, data, UF, BR, km e a
coordenada, presente em todos), o tipo do veículo e o estado físico; o "todas as causas" repete a pessoa por causa e o
"por ocorrência" não diz o veículo, então só o primeiro serve. Um acidente é com moto quando algum veículo dele é
motocicleta, motoneta ou ciclomotor; motociclista morto é quem estava num deles e morreu.

Só saem contagens por lugar (UF, BR, km inteiro) dos 12 meses mais novos publicados; os arquivos da PRF já vêm sem nome
nem placa. Medido em 2026-10-09 (setembro de 2025 a agosto de 2026): 73.978 acidentes, 33.323 com moto, 2.208
motociclistas mortos.
"""
from __future__ import annotations

import csv
import io
import os
import re
import time
import zipfile
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from datakit.sources import prf_fines
from datakit.sources._http import get_text

PAGE = prf_fines.PAGE
FOLDER = "prf_acidentes"
MOTO = ("motocicleta", "motoneta", "ciclomotor")

_ROW = re.compile(r"<tr\b.*?</tr>", re.S | re.I)
_YEAR = re.compile(r"CSV\s+de\s+Acidentes\s+(\d{4})\s*\(\s*Agrupados\s+por\s+pessoa\s*\)", re.I)
_FILE = re.compile(r"drive\.google\.com/file/d/([\w-]+)")


@dataclass(frozen=True)
class Accident:
    """Um acidente: o mês (AAAA-MM), o lugar (UF, BR, km inteiro), se tinha moto e quantos motociclistas morreram."""
    month: str
    uf: str
    br: int
    km: int
    moto: bool
    rider_deaths: int


def yearly_files(page: str) -> Dict[int, str]:
    """{ano: id do arquivo no Drive} das linhas "Acidentes <ano> (Agrupados por pessoa)"; o primeiro link da linha."""
    found: Dict[int, str] = {}
    for row in _ROW.findall(page):
        year, file = _YEAR.search(row), _FILE.search(row)
        if year and file:
            found.setdefault(int(year.group(1)), file.group(1))
    if not found:
        raise ValueError("a página de dados abertos da PRF não tem os acidentes agrupados por pessoa")
    return found


def _place(row: Dict[str, str]) -> Optional[Tuple[str, str, int, int]]:
    date = (row.get("data_inversa") or "").strip()
    month = date[:7] if re.match(r"\d{4}-\d{2}", date) else (f"{date[6:10]}-{date[3:5]}" if re.match(r"\d{2}/\d{2}/\d{4}", date) else "")
    try:
        return month, row["uf"].strip(), int(row["br"]), int(float(row["km"].replace(",", ".")))
    except (ValueError, KeyError):
        return None


def read(path: str) -> Dict[str, Accident]:
    """{id: acidente} do zip de um ano; a linha sem data, BR ou km fica de fora."""
    out: Dict[str, Accident] = {}
    with zipfile.ZipFile(path) as z:
        for info in z.infolist():
            if not info.filename.lower().endswith(".csv"):
                continue
            with z.open(info) as raw:
                for row in csv.DictReader(io.TextIOWrapper(raw, encoding="latin-1", newline=""), delimiter=";"):
                    place = _place(row)
                    if place is None or not place[0]:
                        continue
                    moto = any(m in (row.get("tipo_veiculo") or "").lower() for m in MOTO)
                    dead = moto and (row.get("mortos") == "1" or (row.get("estado_fisico") or "").lower().startswith("óbito"))
                    seen = out.get(row["id"])
                    if seen is None:
                        out[row["id"]] = Accident(*place, moto=moto, rider_deaths=int(dead))
                    else:
                        out[row["id"]] = Accident(seen.month, seen.uf, seen.br, seen.km, seen.moto or moto,
                                                  seen.rider_deaths + int(dead))
    return out


def _fetch(file_id: str, dest: str) -> None:
    prf_fines._fetch(file_id, dest)


def load(raw_dir: str) -> Tuple[Tuple[str, str], List[Accident]]:
    """((primeiro mês, último mês), acidentes com moto) dos 12 meses mais novos, dos zips dos dois anos mais novos."""
    files = yearly_files(get_text(PAGE))
    folder = os.path.join(raw_dir, FOLDER)
    os.makedirs(folder, exist_ok=True)
    accidents: Dict[str, Accident] = {}
    for year in sorted(files, reverse=True)[:2]:
        dest = os.path.join(folder, f"{files[year]}.zip")
        if not (os.path.exists(dest) and time.time() - os.path.getmtime(dest) < prf_fines.FRESH_S):
            _fetch(files[year], dest)
        accidents.update(read(dest))
    if not accidents:
        return ("", ""), []
    last = max(a.month for a in accidents.values())
    first = prf_fines.first_month(last, 12)
    return (first, last), [a for a in accidents.values() if a.moto and first <= a.month <= last]
