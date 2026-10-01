"""IBGE — CNEFE do Censo 2022: cadastro de endereços com coordenada, usado como geocodificador.

`ftp.ibge.gov.br/Cadastro_Nacional_de_Enderecos_para_Fins_Estatisticos/Censo_Demografico_2022/
Arquivos_CNEFE/CSV/Municipio/<cod>_<UF>/<cod7>_<NOME>.zip`: um CSV por município (`;`), com tipo,
título e nome do logradouro, número, LATITUDE, LONGITUDE e NV_GEO_COORD (1 e 2 = coordenada do
próprio endereço; 3 a 6 = estimada, da face de quadra, da localidade ou do setor: não usadas).
É o retrato do Censo, não muda: o arquivo baixado uma vez serve para sempre (data/raw/cnefe/).

Aqui: achar o arquivo do município, ler só as ruas que interessam e resolver "rua + número" e
"cruzamento de duas ruas" num ponto. Quem decide o que procurar é datakit/inmetro_addresses.py.
Medição da precisão em FONTES.md (mediana de 23 m e 89% a até 100 m, em cinco capitais).
"""
from __future__ import annotations

import csv
import difflib
import io
import os
import re
import statistics
import urllib.parse
import zipfile
from typing import Dict, Iterable, List, Optional, Tuple

from datakit.common.address import street_key
from datakit.common.ufpoly import IBGE_UF
from datakit.sources._http import get_bytes, get_text

BASE = ("https://ftp.ibge.gov.br/Cadastro_Nacional_de_Enderecos_para_Fins_Estatisticos/"
        "Censo_Demografico_2022/Arquivos_CNEFE/CSV/Municipio")
FOLDER = "cnefe"
UF_CODE = {uf: code for code, uf in IBGE_UF.items()}

# Nome parecido só vale com o mesmo último nome ("MINISTRO JOSE AMERCO DE ALMEIDA" ~ "… AMERICO DE
# ALMEIDA"); sem isso, ruas vizinhas de nome parecido se confundem.
NAME_CUTOFF = 0.86
# Só aceita o número se o cadastro tiver, na mesma rua, um endereço a até esta diferença de número:
# longe disso a interpolação vira chute (medido: 86% a até 100 m com o filtro, 75% sem ele).
MAX_NUMBER_GAP = 20
# Duas ruas "se cruzam" se têm endereços a até isto um do outro.
MAX_CROSSING_GAP_M = 80.0

Point = Tuple[int, float, float]   # (número, lat, lng)


def _name_key(name: str) -> str:
    """Nome de município comparável: maiúsculas, sem acento, só letras e números."""
    import unicodedata
    return re.sub(r"[^A-Z0-9]", "", unicodedata.normalize("NFKD", name.upper()).encode("ascii", "ignore").decode())


def municipality_files(uf: str) -> Dict[str, str]:
    """{nome do município sem acento nem espaço: nome do arquivo .zip} da pasta da UF no IBGE."""
    html = get_text(f"{BASE}/{UF_CODE[uf]}_{uf}/")
    out: Dict[str, str] = {}
    for href in re.findall(r'href="(\d{7}_[^"]+\.zip)"', html):
        name = urllib.parse.unquote(href)[8:-4].replace("_", " ")
        out[_name_key(name)] = href
    return out


# Municípios que mudaram de nome ou de grafia depois do nome usado nos arquivos do cadastro.
_OLD_NAMES = {"EMBUDASARTES": "EMBU", "MOGIMIRIM": "MOJIMIRIM"}


def file_for(municipality: str, files: Dict[str, str]) -> Optional[str]:
    key = _name_key(municipality)
    return files.get(key) or files.get(_OLD_NAMES.get(key, ""))


def ensure(uf: str, href: str, raw_dir: str) -> str:
    """Caminho do .zip do município em data/raw/cnefe/, baixando se ainda não estiver lá."""
    folder = os.path.join(raw_dir, FOLDER)
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, urllib.parse.unquote(href))
    if not os.path.exists(path):
        data = get_bytes(f"{BASE}/{UF_CODE[uf]}_{uf}/{href}", timeout=600)
        tmp = path + ".part"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    return path


def read_streets(zip_path: str, wanted: Iterable[str]) -> Dict[str, List[Point]]:
    """{chave da rua: [(número, lat, lng)] em ordem de número} das ruas do arquivo que podem ser
    alguma das procuradas: as que terminam no mesmo último nome (é o que match_street exige). Uma
    capital tem milhões de endereços; guardar só essas mantém a memória pequena."""
    last = {w.split()[-1] for w in wanted if w}
    streets: Dict[str, List[Point]] = {}
    keys: Dict[Tuple[str, str], str] = {}      # (título, nome) como no arquivo -> chave
    with zipfile.ZipFile(zip_path) as z:
        with z.open(z.namelist()[0]) as raw:
            rows = csv.reader(io.TextIOWrapper(raw, encoding="utf-8", errors="replace", newline=""), delimiter=";")
            header = next(rows)
            col = {name: i for i, name in enumerate(header)}
            i_title, i_name, i_num = col["NOM_TITULO_SEGLOGR"], col["NOM_SEGLOGR"], col["NUM_ENDERECO"]
            i_lat, i_lng, i_level = col["LATITUDE"], col["LONGITUDE"], col["NV_GEO_COORD"]
            for r in rows:
                if len(r) <= i_level or r[i_level] not in ("1", "2"):
                    continue
                raw_key = (r[i_title], r[i_name])
                key = keys.get(raw_key)
                if key is None:
                    key = keys[raw_key] = street_key(f"{r[i_title]} {r[i_name]}")
                if not key or key.rsplit(" ", 1)[-1] not in last:
                    continue
                try:
                    number = int(r[i_num])
                    lat, lng = float(r[i_lat]), float(r[i_lng])
                except ValueError:
                    continue
                if number > 0:
                    streets.setdefault(key, []).append((number, lat, lng))
    for pts in streets.values():
        pts.sort()
    return streets


def match_street(key: str, streets: Dict[str, List[Point]]) -> Optional[str]:
    """A rua do cadastro com esta chave: igual, ou a mais parecida com o mesmo último nome."""
    if key in streets:
        return key
    last = key.split()[-1]
    close = [c for c in difflib.get_close_matches(key, list(streets), n=5, cutoff=NAME_CUTOFF)
             if c.split()[-1] == last]
    return close[0] if close else None


def locate_number(points: List[Point], number: int) -> Optional[Tuple[float, float]]:
    """(lat, lng) do número na rua: a mediana dos endereços com esse número; sem ele, entre o
    menor e o maior mais próximos (interpolado), ou o do mais próximo. None se o cadastro não tem
    endereço a até MAX_NUMBER_GAP do número."""
    same = [p for p in points if p[0] == number]
    if same:
        return statistics.median(p[1] for p in same), statistics.median(p[2] for p in same)
    lower = [p for p in points if p[0] < number]
    upper = [p for p in points if p[0] > number]
    gaps = ([number - lower[-1][0]] if lower else []) + ([upper[0][0] - number] if upper else [])
    if not gaps or min(gaps) > MAX_NUMBER_GAP:
        return None
    if lower and upper:
        a, b = lower[-1], upper[0]
        t = (number - a[0]) / (b[0] - a[0])
        return a[1] + t * (b[1] - a[1]), a[2] + t * (b[2] - a[2])
    p = lower[-1] if lower else upper[0]
    return p[1], p[2]


def crossing_point(a: List[Point], b: List[Point]) -> Optional[Tuple[float, float]]:
    """(lat, lng) do cruzamento: o meio do par de endereços mais próximos entre as duas ruas; None
    se nenhum par fica a até MAX_CROSSING_GAP_M (as ruas não se cruzam, ou não há endereço perto)."""
    import numpy as np

    pa = np.array([(p[1], p[2]) for p in a])
    pb = np.array([(p[1], p[2]) for p in b])
    m_lat = 111_195.0
    m_lng = m_lat * float(np.cos(np.radians(pa[0, 0])))
    best: Optional[Tuple[float, Tuple[float, float]]] = None
    for start in range(0, len(pa), 2000):          # em blocos: a matriz inteira de uma avenida longa não cabe
        chunk = pa[start:start + 2000]
        d = np.hypot((chunk[:, None, 0] - pb[None, :, 0]) * m_lat, (chunk[:, None, 1] - pb[None, :, 1]) * m_lng)
        i, j = np.unravel_index(int(d.argmin()), d.shape)
        if best is None or d[i, j] < best[0]:
            best = (float(d[i, j]), ((chunk[i, 0] + pb[j, 0]) / 2, (chunk[i, 1] + pb[j, 1]) / 2))
    if best is None or best[0] > MAX_CROSSING_GAP_M:
        return None
    return float(best[1][0]), float(best[1][1])
