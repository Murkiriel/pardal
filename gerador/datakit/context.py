"""O estado de uma execução do build num objeto só, e o contrato das fontes.

Antes, cada peça guardava o que já tinha carregado numa variável do próprio módulo (o extrato do
OSM da região e as fontes oficiais em build.py, os locais desativados em cet_sp, as rotas do SNV
em snv, os polígonos das UFs em ufpoly): duas execuções no mesmo processo (os testes) se
misturavam, e o que cada função usava não aparecia na assinatura. Agora vive tudo no `BuildContext`,
criado pelo build e passado adiante.

Contrato das fontes oficiais: cada módulo expõe `fetch(ctx) -> SourceData` (radares, limites e
locais que o órgão desativou). Fonte que falha é registrada em `falhas` com o nome dela e o build
segue sem ela; fonte com várias bases (DER-SP, capitais) registra cada base que falhar.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from datakit import failures
from datakit.common.model import Camera, Limit

_NOT_LOADED = object()


@dataclass
class SourceData:
    """O que uma fonte devolve."""
    cameras: List[Camera] = field(default_factory=list)
    limits: List[Limit] = field(default_factory=list)
    deactivated: List[Tuple[float, float]] = field(default_factory=list)   # (lat, lng)


def _try(label: str, fn, default):
    try:
        return fn()
    except Exception as e:  # noqa: BLE001
        failures.record(label, e)
        return default


class BuildContext:
    """Uma execução do build. `sources`: {nome: módulo com fetch(ctx)}, na ordem do manifest."""

    def __init__(self, raw_dir: str, refresh_osm: bool = True, sources: Optional[dict] = None):
        self.raw_dir = raw_dir
        self.refresh_osm = refresh_osm
        self.sources = sources or {}
        self._polygons: Optional[dict] = None
        self._routes = _NOT_LOADED
        self._official: Optional[Dict[str, SourceData]] = None
        self._national: Optional[dict] = None
        self._osm_region: Optional[str] = None
        self._bumps_region: Optional[str] = None
        self._bumps: list = []
        self._tolls_region: Optional[str] = None
        self._tolls: list = []
        self._antt_tolls: Optional[list] = None
        self._prf_radar: Optional[tuple] = None
        self._potholes: Optional[tuple] = None
        self._osm: Optional[tuple] = None
        self._inmetro_points: Dict[str, List[Camera]] = {}

    # ── bases carregadas uma vez por execução ──────────────────────────────────

    def polygons(self) -> dict:
        """{UF: polígono} da malha do IBGE; {} sem shapely ou sem a malha."""
        if self._polygons is None:
            from datakit.common import ufpoly
            self._polygons = ufpoly.polygons(self.raw_dir)
        return self._polygons

    def snv_routes(self):
        """Rotas do SNV (SnvRoutes), ou None se o SNV falhar (a falha é registrada uma vez)."""
        if self._routes is _NOT_LOADED:
            from datakit.sources import snv
            self._routes = snv.load_routes(self.raw_dir)
        return self._routes

    def official(self) -> Dict[str, SourceData]:
        """Carga nacional de cada fonte oficial (sem recorte)."""
        if self._official is None:
            self._official = {}
            for name, mod in self.sources.items():
                try:
                    loaded = mod.fetch(self)
                    print(f"[build] {name}: {len(loaded.cameras)} radares (nacional)")
                except NotImplementedError as e:
                    loaded = SourceData()
                    print(f"[build] {name}: pulado ({e})")
                except Exception as e:  # noqa: BLE001
                    loaded = SourceData()
                    failures.record(name, e)
                self._official[name] = loaded
        return self._official

    def osm(self, region: str, probes=None):
        """(extrato, radares, limites, estruturas, sentidos das sondas) do OSM da região. Só a
        região atual fica em memória: as UFs saem agrupadas por região."""
        if self._osm_region != region:
            from datakit.sources import osm_pbf
            self._osm = None   # libera a região anterior antes de carregar a próxima
            ex = osm_pbf.ensure_extract(region, self.raw_dir, refresh=self.refresh_osm)
            print(f"[build] extrato {os.path.basename(ex.path)}  {ex.bytes/1e6:.1f} MB  ({ex.file_date})")
            probe_dirs: dict = {}
            cams, lims, structs = osm_pbf.load(ex.path, bbox=None, probes=probes, probe_out=probe_dirs)
            print(f"[build] OSM/{region}: {len(cams)} radares, {len(lims)} limites, "
                  f"{len(structs)} pontes/túneis, {len(probe_dirs)} sentidos nominais resolvidos")
            self._osm_region, self._osm = region, (ex, cams, lims, structs, probe_dirs)
        return self._osm

    def osm_bumps(self, region: str) -> list:
        """Lombadas e quebra-molas do OSM da região (osm_pbf.load_bumps, uma passada a mais no extrato que osm()
        já baixou). Só a região atual fica em memória, como em osm()."""
        if self._bumps_region != region:
            from datakit.sources import osm_pbf
            self._bumps = []
            osm = self._osm if self._osm_region == region else None
            ex = osm[0] if osm is not None else osm_pbf.ensure_extract(region, self.raw_dir, refresh=False)
            self._bumps = osm_pbf.load_bumps(ex.path)
            self._bumps_region = region
            print(f"[build] OSM/{region}: {len(self._bumps)} lombadas e quebra-molas")
        return self._bumps

    def osm_tolls(self, region: str) -> list:
        """Pedágios do OSM da região (osm_pbf.load_tolls), uma passada a mais no extrato que osm() já baixou."""
        if self._tolls_region != region:
            from datakit.sources import osm_pbf
            self._tolls = []
            osm = self._osm if self._osm_region == region else None
            ex = osm[0] if osm is not None else osm_pbf.ensure_extract(region, self.raw_dir, refresh=False)
            self._tolls = osm_pbf.load_tolls(ex.path)
            self._tolls_region = region
            print(f"[build] OSM/{region}: {len(self._tolls)} cabines e pórticos de pedágio")
        return self._tolls

    def antt_tolls(self) -> list:
        """As praças de pedágio federais da ANTT, baixadas uma vez por execução; falha vira lista vazia, registrada."""
        if self._antt_tolls is None:
            from datakit import failures
            from datakit.sources import antt_pedagio
            try:
                self._antt_tolls = antt_pedagio.load()
                print(f"[build] ANTT: {len(self._antt_tolls)} praças de pedágio")
            except Exception as e:  # noqa: BLE001 - uma fonte fora do ar não derruba a geração
                failures.record("ANTT (pedágios)", e)
                self._antt_tolls = []
        return self._antt_tolls

    def dnit_potholes(self) -> tuple:
        """(mês, km com buracos) do levantamento mais novo do ICM do DNIT, baixado uma vez por execução; falha vira
        ("", []), registrada."""
        if self._potholes is None:
            from datakit import failures
            from datakit.sources import dnit_icm
            try:
                self._potholes = dnit_icm.load()
                print(f"[build] DNIT: {len(self._potholes[1])} km com buracos (ICM de {self._potholes[0]})")
            except Exception as e:  # noqa: BLE001 - uma fonte fora do ar não derruba a geração
                failures.record("DNIT (buracos, ICM)", e)
                self._potholes = ("", [])
        return self._potholes

    def prf_portable_radar(self) -> tuple:
        """(início da validade, trechos) da lista vigente da PRF de trechos aptos ao radar portátil, baixada uma vez por
        execução; falha vira ("", []), registrada."""
        if self._prf_radar is None:
            from datakit import failures
            from datakit.sources import prf_portable_radar
            try:
                valid_from, stretches, skipped = prf_portable_radar.load()
                print(f"[build] PRF: {len(stretches)} trechos aptos ao radar portátil, lista de {valid_from} "
                      f"({skipped} linhas de fora)")
                self._prf_radar = (valid_from, stretches)
            except Exception as e:  # noqa: BLE001 - uma fonte fora do ar não derruba a geração
                failures.record("PRF (trechos de radar portátil)", e)
                self._prf_radar = ("", [])
        return self._prf_radar

    def inmetro_points(self, uf: str) -> List[Camera]:
        """Locais do Inmetro só com endereço, localizados pelo cadastro do IBGE (inmetro_addresses.py),
        do arquivo do Inmetro que cobre a UF: o da própria UF, ou o de Goiás para o DF (os medidores
        de Brasília vêm nele). Um arquivo é geocodificado uma vez por execução."""
        file_uf = "GO" if uf == "DF" else uf
        if file_uf not in self._inmetro_points:
            from datakit import inmetro_addresses
            meters = [m for m in self.national()["meter_list"] if m.uf == file_uf]
            self._inmetro_points[file_uf] = _try(
                f"Inmetro {file_uf} (endereços)", lambda: inmetro_addresses.geocode(meters, file_uf, self.raw_dir), [])
        return self._inmetro_points[file_uf]

    def national(self) -> dict:
        """SNV (rotas + concessões), Inmetro e os limites oficiais. Cada base que falhar fica
        vazia — o pacote sai só sem aquela parte."""
        if self._national is None:
            from datakit import inmetro_status
            from datakit.common.lrs import load_concessions
            from datakit.sources import antt_placas, cet_sp, inmetro, rio, snv
            raw = self.raw_dir
            paths = _try("SNV", lambda: snv.ensure(raw), None)
            routes = self.snv_routes() if paths else None
            conc = _try("SNV concessões", lambda: load_concessions(paths[1]), {}) if paths else {}
            meters = _try("Inmetro", lambda: inmetro.load(raw), [])
            print(f"[build] Inmetro: {len(meters)} medidores; SNV {paths[2] if paths else '—'}")
            antt_l = _try("ANTT placas", lambda: antt_placas.load(raw, routes), []) if routes else []
            rio_l = _try("Rio trechos", rio.load_limits, [])
            cet_l = _try("CET-SP", cet_sp.load_limits, [])
            print(f"[build] limites oficiais: ANTT {len(antt_l)}, Rio {len(rio_l)}, CET-SP {len(cet_l)} pontos")
            self._national = dict(snv=routes, concessions=conc, snv_version=paths[2] if paths else None,
                                  meters=inmetro_status.index_meters(meters), n_meters=len(meters),
                                  meter_list=meters,
                                  official_limits={"ANTT": antt_l, "RIO": rio_l, "CET-SP": cet_l})
        return self._national
