"""Em que UF fica cada ponto — decidido uma vez, e cada ponto em exatamente uma UF.

1. o polígono da UF que contém o ponto;
2. se nenhum contém: a UF de polígono mais perto, a até NEAR_M. A malha simplificada do IBGE
   corta a linha d'água, e ponte, orla e ilha ficam "no mar" (os 12 radares da ANTT na Ponte
   Rio-Niterói ficavam fora de todos os pacotes; o mais longe está a ~2,8 km do polígono do RJ);
3. se nada a NEAR_M: nenhuma (fora do Brasil, ou mar aberto).

Antes, cada UF recortava a lista inteira pelo próprio polígono: o que caía fora de todos sumia,
e o trabalho se repetia por UF.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from datakit.common.geo import haversine_m
from datakit.common.spatial import lat_span_deg, lng_span_deg

NEAR_M = 5000.0


class UfAssigner:
    def __init__(self, polys: Dict[str, Any], near_m: float = NEAR_M):
        import shapely
        self.ufs = sorted(polys)
        self.polys = [polys[u] for u in self.ufs]
        for p in self.polys:
            shapely.prepare(p)
        self.bounds = [p.bounds for p in self.polys]   # (lng mín, lat mín, lng máx, lat máx)
        self.near_m = near_m

    def uf_of(self, lat: float, lng: float) -> Optional[str]:
        return self.assign([lat], [lng])[0]

    def distance_m(self, uf: str, lat: float, lng: float) -> float:
        """Distância do ponto ao polígono da UF, em metros (0 dentro dela)."""
        import shapely
        line = shapely.shortest_line(self.polys[self.ufs.index(uf)], shapely.points(lng, lat))
        sx, sy = shapely.get_coordinates(line)[0]
        return haversine_m((lat, lng), (sy, sx))

    def assign(self, lats: Sequence[float], lngs: Sequence[float]) -> List[Optional[str]]:
        import numpy as np
        import shapely

        x = np.asarray(lngs, dtype=float)
        y = np.asarray(lats, dtype=float)
        k_of = np.full(len(x), -1, dtype=np.int64)
        for k, p in enumerate(self.polys):
            mnx, mny, mxx, mxy = self.bounds[k]
            idx = np.nonzero((k_of < 0) & (x >= mnx) & (x <= mxx) & (y >= mny) & (y <= mxy))[0]
            if len(idx):
                k_of[idx[shapely.contains_xy(p, x[idx], y[idx])]] = k

        rest = np.nonzero(k_of < 0)[0]
        if len(rest):
            best = np.full(len(rest), np.inf)
            dlat = lat_span_deg(self.near_m)
            for k, p in enumerate(self.polys):
                mnx, mny, mxx, mxy = self.bounds[k]
                dlng = lng_span_deg(self.near_m, max(abs(mny), abs(mxy)))
                sel = np.nonzero((x[rest] >= mnx - dlng) & (x[rest] <= mxx + dlng)
                                 & (y[rest] >= mny - dlat) & (y[rest] <= mxy + dlat))[0]
                if not len(sel):
                    continue
                pts = shapely.points(x[rest[sel]], y[rest[sel]])
                # dlng é o maior raio em graus (o grau de longitude é o mais curto): superconjunto
                sel = sel[shapely.dwithin(p, pts, dlng)]
                if not len(sel):
                    continue
                lines = shapely.shortest_line(p, shapely.points(x[rest[sel]], y[rest[sel]]))
                starts = shapely.get_coordinates(shapely.get_point(lines, 0))
                for j, (sx, sy) in zip(sel, starts):
                    d = haversine_m((y[rest[j]], x[rest[j]]), (sy, sx))
                    if d <= self.near_m and d < best[j]:
                        best[j] = d
                        k_of[rest[j]] = k
        return [self.ufs[k] if k >= 0 else None for k in k_of.tolist()]
