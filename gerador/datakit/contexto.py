"""O estado de uma execução do build num objeto só, e o contrato das fontes.

Antes, cada peça guardava o que já tinha carregado numa variável do próprio módulo (o extrato do
OSM da região e as fontes oficiais em build.py, os locais desativados em cet_sp, as rotas do SNV
em snv, os polígonos das UFs em ufpoly): duas execuções no mesmo processo (os testes) se
misturavam, e o que cada função usava não aparecia na assinatura. Agora vive tudo no `Contexto`,
criado pelo build e passado adiante.

Contrato das fontes oficiais: cada módulo expõe `carregar(ctx) -> Carga` (radares, limites e
locais que o órgão desativou). Fonte que falha é registrada em `falhas` com o nome dela e o build
segue sem ela; fonte com várias bases (DER-SP, capitais) registra cada base que falhar.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from datakit import falhas
from datakit.common.model import Camera, Limit

_NAO_CARREGADO = object()


@dataclass
class Carga:
    """O que uma fonte devolve."""
    radares: List[Camera] = field(default_factory=list)
    limites: List[Limit] = field(default_factory=list)
    desativados: List[Tuple[float, float]] = field(default_factory=list)   # (lat, lng)


def _try(label: str, fn, default):
    try:
        return fn()
    except Exception as e:  # noqa: BLE001
        falhas.registrar(label, e)
        return default


class Contexto:
    """Uma execução do build. `fontes`: {nome: módulo com carregar(ctx)}, na ordem do manifest."""

    def __init__(self, raw_dir: str, refresh_osm: bool = True, fontes: Optional[dict] = None):
        self.raw_dir = raw_dir
        self.refresh_osm = refresh_osm
        self.fontes = fontes or {}
        self._polygons: Optional[dict] = None
        self._rotas = _NAO_CARREGADO
        self._oficiais: Optional[Dict[str, Carga]] = None
        self._nacional: Optional[dict] = None
        self._osm_region: Optional[str] = None
        self._osm: Optional[tuple] = None

    # ── bases carregadas uma vez por execução ──────────────────────────────────

    def polygons(self) -> dict:
        """{UF: polígono} da malha do IBGE; {} sem shapely ou sem a malha."""
        if self._polygons is None:
            from datakit.common import ufpoly
            self._polygons = ufpoly.polygons(self.raw_dir)
        return self._polygons

    def rotas_snv(self):
        """Rotas do SNV (SnvRoutes), ou None se o SNV falhar (a falha é registrada uma vez)."""
        if self._rotas is _NAO_CARREGADO:
            from datakit.sources import snv
            self._rotas = snv.load_routes(self.raw_dir)
        return self._rotas

    def oficiais(self) -> Dict[str, Carga]:
        """Carga nacional de cada fonte oficial (sem recorte)."""
        if self._oficiais is None:
            self._oficiais = {}
            for name, mod in self.fontes.items():
                try:
                    carga = mod.carregar(self)
                    print(f"[build] {name}: {len(carga.radares)} radares (nacional)")
                except NotImplementedError as e:
                    carga = Carga()
                    print(f"[build] {name}: pulado ({e})")
                except Exception as e:  # noqa: BLE001
                    carga = Carga()
                    falhas.registrar(name, e)
                self._oficiais[name] = carga
        return self._oficiais

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

    def nacional(self) -> dict:
        """SNV (rotas + concessões), Inmetro e os limites oficiais. Cada base que falhar fica
        vazia — o pacote sai só sem aquela parte."""
        if self._nacional is None:
            from datakit import inmetro_status
            from datakit.common.lrs import load_concessions
            from datakit.sources import antt_placas, cet_sp, inmetro, rio, snv
            raw = self.raw_dir
            paths = _try("SNV", lambda: snv.ensure(raw), None)
            routes = self.rotas_snv() if paths else None
            conc = _try("SNV concessões", lambda: load_concessions(paths[1]), {}) if paths else {}
            meters = _try("Inmetro", lambda: inmetro.load(raw), [])
            print(f"[build] Inmetro: {len(meters)} medidores; SNV {paths[2] if paths else '—'}")
            antt_l = _try("ANTT placas", lambda: antt_placas.load(raw, routes), []) if routes else []
            rio_l = _try("Rio trechos", rio.load_limits, [])
            cet_l = _try("CET-SP", cet_sp.load_limits, [])
            print(f"[build] limites oficiais: ANTT {len(antt_l)}, Rio {len(rio_l)}, CET-SP {len(cet_l)} pontos")
            self._nacional = dict(snv=routes, concessions=conc, snv_version=paths[2] if paths else None,
                                  meters=inmetro_status.index_meters(meters), n_meters=len(meters),
                                  official_limits={"ANTT": antt_l, "RIO": rio_l, "CET-SP": cet_l})
        return self._nacional
