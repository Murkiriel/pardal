"""UFs brasileiras: bbox aproximada + a região da Geofabrik que contém cada uma.

A Geofabrik não recorta o Brasil por estado — só nas 5 macrorregiões. O pipeline baixa
a macrorregião e recorta pela UF (polígono do IBGE quando disponível, ver ufpoly.py;
senão por esta bbox).
"""
from __future__ import annotations

from typing import Dict, Tuple

BBox = Tuple[float, float, float, float]  # (min_lat, min_lng, max_lat, max_lng)

# Região Geofabrik em south-america/brazil/<região>-latest.osm.pbf
GEOFABRIK_REGION: Dict[str, str] = {
    **{uf: "norte" for uf in ("AC", "AP", "AM", "PA", "RO", "RR", "TO")},
    **{uf: "nordeste" for uf in ("AL", "BA", "CE", "MA", "PB", "PE", "PI", "RN", "SE")},
    **{uf: "centro-oeste" for uf in ("DF", "GO", "MT", "MS")},
    **{uf: "sudeste" for uf in ("ES", "MG", "RJ", "SP")},
    **{uf: "sul" for uf in ("PR", "RS", "SC")},
}

# bbox aproximada por UF — só para recorte grosseiro
UF_BBOX: Dict[str, BBox] = {
    "AC": (-11.15, -74.02, -7.11, -66.62),
    "AL": (-10.51, -38.24, -8.81, -35.15),
    "AM": (-9.82, -73.80, 2.25, -56.10),
    "AP": (-1.24, -54.88, 4.44, -49.87),
    "BA": (-18.35, -46.62, -8.53, -37.34),
    "CE": (-7.86, -41.42, -2.78, -37.25),
    "DF": (-16.05, -48.29, -15.50, -47.31),
    "ES": (-21.30, -41.88, -17.89, -39.66),
    "GO": (-19.50, -53.25, -12.40, -45.90),
    "MA": (-10.26, -48.75, -1.04, -41.80),
    "MG": (-22.92, -51.05, -14.23, -39.86),
    "MS": (-24.07, -58.17, -17.16, -50.92),
    "MT": (-18.04, -61.63, -7.35, -50.22),
    "PA": (-9.84, -58.90, 2.59, -46.06),
    "PB": (-8.30, -38.77, -6.02, -34.79),
    "PE": (-9.48, -41.36, -7.27, -34.81),
    "PI": (-10.93, -45.99, -2.74, -40.37),
    "PR": (-26.72, -54.62, -22.52, -48.02),
    "RJ": (-23.37, -44.89, -20.76, -40.96),
    "RN": (-6.99, -38.58, -4.83, -34.96),
    "RO": (-13.69, -66.81, -7.97, -59.77),
    "RR": (-1.58, -64.82, 5.27, -58.89),
    "RS": (-33.75, -57.65, -27.08, -49.69),
    "SC": (-29.36, -53.84, -25.95, -48.35),
    "SE": (-11.57, -38.25, -9.51, -36.39),
    "SP": (-25.31, -53.11, -19.78, -44.16),
    "TO": (-13.47, -50.74, -5.17, -45.70),
}


UF_NAME: Dict[str, str] = {
    "AC": "Acre", "AL": "Alagoas", "AM": "Amazonas", "AP": "Amapá", "BA": "Bahia",
    "CE": "Ceará", "DF": "Distrito Federal", "ES": "Espírito Santo", "GO": "Goiás",
    "MA": "Maranhão", "MG": "Minas Gerais", "MS": "Mato Grosso do Sul", "MT": "Mato Grosso",
    "PA": "Pará", "PB": "Paraíba", "PE": "Pernambuco", "PI": "Piauí", "PR": "Paraná",
    "RJ": "Rio de Janeiro", "RN": "Rio Grande do Norte", "RO": "Rondônia", "RR": "Roraima",
    "RS": "Rio Grande do Sul", "SC": "Santa Catarina", "SE": "Sergipe",
    "SP": "São Paulo", "TO": "Tocantins",
}


# UF de cada órgão estadual, distrital ou municipal, pelo `source` dos radares dele. As fontes
# federais (DNIT, ANTT) e o OSM cobrem o país inteiro e não entram aqui.
SOURCE_HOME_UF: Dict[str, str] = {
    "DER-SP": "SP", "DER-GO": "GO", "DER-PE": "PE", "DER-MG": "MG", "DETRAN-DF": "DF",
    "CET-SP": "SP", "RIO": "RJ", "BHTRANS": "MG", "PMF": "CE", "PCR": "PE", "PMJP": "PB", "CURITIBA": "PR", "EPTC": "RS",
}


def uf_name(uf: str) -> str:
    return UF_NAME.get(uf.upper(), uf.upper())


def geofabrik_region(uf: str) -> str:
    return GEOFABRIK_REGION[uf.upper()]


def uf_bbox(uf: str) -> BBox:
    return UF_BBOX[uf.upper()]
