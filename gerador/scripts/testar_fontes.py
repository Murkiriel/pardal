"""Diagnóstico das fontes: cada endereço que o gerador usa responde daqui?

    python scripts/testar_fontes.py                 # tabela no terminal
    python scripts/testar_fontes.py --json saida.json

Faz uma requisição leve por fonte (a mesma rota do gerador: Python/requests, ou curl onde o
gerador usa curl), sem novas tentativas, e mede status e tempo. Serve para descobrir, antes de
um build longo, se alguma fonte está fora do ar ou bloqueia o lugar de onde se roda (por
exemplo, as máquinas do GitHub Actions, fora do Brasil). Não grava nada em data/.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests  # noqa: E402

from datakit.common import ufpoly  # noqa: E402
from datakit.sources import (  # noqa: E402
    _powerbi, antt, antt_placas, bh, cet_sp, der_go, der_pe, der_sp, df_detran, dnit, inmetro,
    municipal, osm_pbf, rio, snv,
)
from datakit.sources._http import UA  # noqa: E402

TIMEOUT = 60


def _req(method, url, **kw):
    if method == "HEAD":   # sem corpo: ler o fluxo ficaria esperando
        r = requests.head(url, headers={**UA, **kw.pop("headers", {})}, timeout=TIMEOUT, allow_redirects=True, **kw)
        return r.status_code, b""
    r = requests.request(method, url, headers={**UA, **kw.pop("headers", {})}, timeout=TIMEOUT,
                         stream=True, **kw)
    body = r.raw.read(4096, decode_content=True)   # só o começo: basta para ver se é o dado ou um bloqueio
    r.close()
    return r.status_code, body


def _curl(url):
    p = subprocess.run(["curl", "-sSL", "-m", str(TIMEOUT), "-A", "Mozilla/5.0", "-o", os.devnull,
                        "-w", "%{http_code}", url], capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(p.stderr.strip() or f"curl saiu com {p.returncode}")
    return int(p.stdout or 0), b""


def _pbi():
    key = _powerbi.resource_key(cet_sp.PBI_VIEW)
    return 200, str(_powerbi.model_id(cet_sp.PBI_API, key)).encode()


def _webdav():
    r = requests.request("PROPFIND", snv.SHARE + requests.utils.quote(snv.ROTAS), auth=(snv.TOKEN, ""),
                         headers={**UA, "Depth": "1"}, timeout=TIMEOUT)
    return r.status_code, r.content[:4096]


FONTES = [
    # (fonte, o que é, função que faz a requisição)
    ("DNIT (PNCV)", "catálogo CKAN dos radares federais", lambda: _req("GET", dnit.CKAN)),
    ("DNIT (SNV)", "rotas com km, WebDAV", _webdav),
    ("ANTT (radares)", "catálogo CKAN", lambda: _req("GET", antt.CKAN)),
    ("ANTT (placas)", "catálogo CKAN", lambda: _req("GET", antt_placas.CKAN)),
    ("Inmetro", "medidores de SP (json)", lambda: _req("GET", inmetro.URL.format(uf="SP"))),
    ("DER-GO (radares)", "ArcGIS", lambda: _req("GET", der_go.FS, params={"where": "1=1", "returnCountOnly": "true", "f": "json"})),
    ("DER-GO (malha)", "ArcGIS", lambda: _req("GET", der_go.MALHA, params={"where": "1=1", "returnCountOnly": "true", "f": "json"})),
    ("Artesp", "planilha de recursos (xlsx)", lambda: _req("GET", der_sp.ARTESP_XLSX)),
    ("DER-SP", "planilha de radares (xlsx)", lambda: _req("GET", der_sp.DER_SP_OWN_XLSX)),
    ("DER-PE", "página com a tabela", lambda: _req("GET", der_pe.URL)),
    ("João Pessoa", "ArcGIS", lambda: _req("GET", municipal.PMJP_FS, params={"where": "1=1", "returnCountOnly": "true", "f": "json"})),
    ("Fortaleza", "GeoJSON", lambda: _req("GET", municipal.FORTALEZA_GEOJSON)),
    ("Recife", "CSV", lambda: _req("GET", municipal.RECIFE_CSV)),
    ("BH (BHTrans)", "catálogo CKAN (via curl)", lambda: _curl(bh.CKAN)),
    ("Detran-DF", "ArcGIS", lambda: _req("GET", df_detran.BASE, params={"f": "json"})),
    ("Rio (lista)", "página da lista de radares", lambda: _req("GET", rio.LISTA_PAGINA)),
    ("Rio (geocodificador)", "ArcGIS da prefeitura", lambda: _req("GET", rio.GEOCODE, params={"SingleLine": "AVENIDA BRASIL 1000", "f": "json"})),
    ("Rio (trechos)", "ArcGIS da prefeitura", lambda: _req("GET", rio.TRECHOS, params={"where": "1=1", "returnCountOnly": "true", "f": "json"})),
    ("CET-SP (radares)", "Power BI público", _pbi),
    ("CET-SP (limites)", "GeoSampa WFS", lambda: _req("GET", cet_sp.WFS, params={"service": "WFS", "request": "GetCapabilities"})),
    ("OpenStreetMap", "Geofabrik (HEAD do extrato)", lambda: _req("HEAD", f"{osm_pbf.GEOFABRIK_BASE}/norte-latest.osm.pbf")),
    ("IBGE (malha das UFs)", "API de malhas v3", lambda: _req("GET", ufpoly.MESH_URL)),
]


def _parece_bloqueio(body: bytes) -> bool:
    b = body[:2000].lower()
    return any(s in b for s in (b"captcha", b"access denied", b"forbidden", b"cloudflare", b"request rejected",
                                b"location.replace", b"bot protection"))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", help="grava o resultado neste arquivo")
    args = ap.parse_args(argv)
    out = []
    for nome, desc, fn in FONTES:
        t0 = time.time()
        try:
            status, body = fn()
            ok = 200 <= status < 400 and not _parece_bloqueio(body)
            erro = "" if ok else ("página de bloqueio" if _parece_bloqueio(body) else f"HTTP {status}")
        except Exception as e:  # noqa: BLE001
            status, ok, erro = 0, False, f"{type(e).__name__}: {e}"[:200]
        dt = time.time() - t0
        out.append({"fonte": nome, "o_que": desc, "ok": ok, "status": status, "segundos": round(dt, 1), "erro": erro})
        print(f"{'OK  ' if ok else 'FALHA'} {nome:22} {status:>4} {dt:6.1f}s  {desc}{'  -> ' + erro if erro else ''}", flush=True)
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=1)
    falhas = [x["fonte"] for x in out if not x["ok"]]
    print(f"\n{len(out) - len(falhas)}/{len(out)} fontes responderam" + (f"; falharam: {', '.join(falhas)}" if falhas else ""))
    return 1 if falhas else 0


if __name__ == "__main__":
    sys.exit(main())
