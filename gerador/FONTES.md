# Investigação de fontes — radares, limites e estruturas (2026-09-29)

Levantamento para a nova geração dos pacotes antes de publicar o conjunto de dados. Cada
fonte abaixo foi **baixada e aberta** (não só vista num buscador), salvo onde indicado.

Base atual (pacotes de 2026-09-09): 15.702 radares (9.841 OSM, 1.708 DNIT, 1.647 DER-SP,
1.113 ANTT, 1.018 DER-GO, 193 Fortaleza, 79 Recife, 75 João Pessoa, 28 DER-PE), 1,24 milhão
de pontos de limite, 126 mil estruturas.

## Referência nacional: Inmetro (PSIE)

`https://servicos.rbmlq.gov.br/dados-abertos/{UF}/medidores.json` — JSON mensal por UF,
licença Creative Commons (metadados: `dados-abertos/metadados_medidores_velocidade.pdf`).
Todo medidor de velocidade aferido no país, de qualquer órgão: local (`LocalVerificacao`,
rodovia+km ou endereço), município, faixas com sentido e **velocidade**, validade da
aferição e último resultado. **Sem coordenada.** (DF volta vazio; está dentro de GO.)

| | Fixos | Válidos e aprovados em 2026-09-29 | Com rodovia+km |
|---|---|---|---|
| Brasil (26 UFs) | 34.043 | **17.118** | 8.192 |
| GO (inclui Brasília) | 5.433 | 2.760 | 1.709 |
| SP | 8.619 | 4.554 | 1.482 |
| MG | 3.287 | 1.633 | 963 |

Usos: (1) marcar radar ativo/inativo pela aferição, cruzando com as fontes com coordenada;
(2) achar radares que nenhuma outra fonte tem, georreferenciando rodovia+km (ver abaixo) e
endereços (geocodificador).

## A — Com coordenada, prontas para o gerador

| Fonte | O que tem | Formato / licença |
|---|---|---|
| **ANTT — sinalização** (`dados.antt.gov.br/dataset/sinalizacao`) | **14.721 placas de velocidade máxima** das concessões federais: lat/lng, limite leve e pesado, sentido, situação | KMZ, CC-BY, 2026-09-11 |
| **Prefeitura do Rio — trechos de logradouro** (`pgeo3.rio.rj.gov.br/arcgis/rest/services/CadLog/Trechos_Logradouros/MapServer/0`) | `velocidade_regulamentada` em **111.973 de 132.064 trechos** (85% da cidade) | ArcGIS REST; licença a confirmar |
| **Belo Horizonte — fiscalização eletrônica** (`ckan.pbh.gov.br`, `fiscalizacao-eletronica`) | 448 equipamentos: tipo (velocidade, avanço de sinal, faixa exclusiva), limite | CSV mensal, UTM 31983, CC-BY |
| **Belo Horizonte — redutor de velocidade** | 4.748 lombadas físicas | CSV mensal, CC-BY |
| **DF — Detran (ArcGIS `Base_DETRAN`)** | 1.125 radares (lat/lng, limite) + 1.230 lombadas eletrônicas | ArcGIS REST, dados de 2019 |
| **DF — portal de dados abertos** | versão atual do mesmo cadastro | CSV; o portal bloqueia download automático (baixar no navegador) |
| Mogi das Cruzes (ArcGIS `radares`) | 14 radares | ArcGIS REST |

## B — Rodovia + km (precisam de referência linear)

- **DER-MG**: 708 radares fixos com limite (tabela HTML, atualizada 2026-09-29).
- **DER-PR, DAER-RS, DER-ES**: listas em PDF (a obrigação vem da Res. Contran 798/2020).
  DAER-RS não publica o ponto exato dos pardais, só os trechos.
- **Inmetro**: 8.192 válidos com rodovia+km no país.

Referência linear disponível:
- **Federais: SNV Rotas do DNIT** (`servicos.dnit.gov.br/dnitcloud`, pasta "SNV Rotas",
  `rota_202607A.zip`) — 733 rotas PolylineM com o km em cada vértice → interpolação exata.
  A base geométrica (`202607A.zip`, 7.673 trechos) traz `est_coinc` (estadual coincidente).
- **Estaduais**: sem base nacional. Marcos quilométricos no OSM (`highway=milestone`):
  ~11.400 (SC 3.968, SP 1.575, sul federais 1.526; resto esparso). SREs estaduais achados:
  DAER-RS (i3geo), SC (DEINFRA, já importado no OSM); MT (servidor Sinfra fora do ar);
  MG (IDE-Sisema, sem km confirmado).

## C — Só endereço (precisam de geocodificação)

- **Rio de Janeiro** (SMTR/CET-Rio): PDF de 36 páginas com endereço, código e limite
  (atualizado 2026-09-11). A própria prefeitura tem geocodificador com número de porta
  (`pgeo3.../Geocode/Geocode_NP`, `Geocode_Logradouros_WGS84`).
- **Inmetro**: ~8.900 válidos com endereço urbano (Brasília 1.013, Goiânia 291, …).
- Porto Alegre (painel ObservaMob), Curitiba (portal Setran), CET-SP (mapa), Salvador
  (Transalvador, fora do ar no teste), Goiânia (consulta na tela) — sem arquivo aberto.

## D — Indisponíveis agora

- **Seinfra-BA**: publicava KML dos radares estaduais; página suspensa pelo período
  eleitoral. Outros sites estaduais podem estar no mesmo caso até o fim de outubro.
- **SC**: sem radar em rodovia estadual há 14 anos; contrato de retomada suspenso em maio/2026.
- DNIT: o portal só tem `controle-de-velocidade` como dado útil (já usado); sem inventário
  de placas das federais não concedidas.

## Descartadas (licença)

MapaRadar (CC BY-NC-ND), radarnobrasil.com e RecursoDeMulta (colaborativos, sem licença de
redistribuição), bases comerciais, Waze/Google (sem API, termos proíbem).

## Recomendação para esta geração

1. **Entrar agora** (baixo esforço, alto ganho): placas de velocidade da ANTT, trechos do Rio,
   BH (radares + lombadas), DF Detran (ArcGIS), e Inmetro como **status ativo/inativo**
   cruzando por município + rodovia + km com as fontes que já têm coordenada.
2. **Referência linear federal (SNV Rotas)**: georreferenciar os registros "BR-xxx km" do
   Inmetro e do DER-MG que não casarem com nenhuma coordenada existente.
3. **Estaduais por marcos do OSM** onde houver densidade (SC, SP); resto fica para quando
   houver SRE com km.
4. **Geocodificação**: Rio pelo geocodificador da prefeitura; o resto depois.
5. Rodar de novo depois da eleição para BA e outros sites suspensos.

## Resultado da implementação (2026-09-29)

| Frente | O que entrou | Medição |
|---|---|---|
| Placas ANTT | `sources/antt_placas.py`: placa projetada na rota do SNV da própria BR, vale até a próxima do mesmo sentido (máx. 10 km), pontos a cada 200 m; sentidos em desacordo não emitiam nada (desde a seção "Sentido" abaixo: um ponto por sentido) | 14.517 placas ativas, 13.421 casadas no SNV, 33.583 pontos (~6.700 km) |
| Rio — trechos | `sources/rio.py`: vértices + preenchimento a 150 m | 208.986 pontos |
| Rio — radares | PDF da SMTR + geocodificador de número de porta da prefeitura (score ≥ 95) | 1.083 itens (599 velocidade, 484 avanço de sinal), 615 geocodificados |
| BH / DF | `sources/bh.py` (UTM 23S, via curl: o WAF da PBH barra o TLS do Python), `sources/df_detran.py` | 396 / 2.355 |
| Inmetro | `inmetro_status.py`: casa por km na mesma BR/UF fora de concessão; só mexe em OSM, DNIT, DER-GO, Detran-DF | GO: 66 confirmados / 4 desativados; MG: 129 / 21 |
| Limites oficiais x OSM | `model.override_limits`: pontos do OSM a < 50 m de um oficial saem, para o valor não alternar entre as duas fontes no mesmo trecho | — |

**Descartado depois de medir:** criar radar novo a partir de "rodovia + km".
- SNV contra os 1.774 radares do DNIT (coordenada + km): mediana 226 m, p75 873 m, só 34% a ≤ 100 m.
- Marcos quilométricos do OSM (por UF): mediana 83 m, mas 25% > 200 m e só ~100 casos fora de SC.
- O km das placas ANTT é o da concessão (PNV antigo): BR-381/MG km 480,5 cai 7 km longe no SNV atual.

## Sentido dos radares e das placas (2026-09-29)

`direction_deg` em radares e limites: rumo do trânsito fiscalizado (graus a partir do norte),
vazio = os dois sentidos ou desconhecido. Como cada fonte informa o sentido e como vira rumo:

| Fonte | Campo | Conversão para rumo | Medição |
|---|---|---|---|
| ANTT — Radar | `sentido` Crescente/Decrescente (+ `rodovia`, `uf`) | Rota do SNV da BR: direção em que o km cresce no ponto (±50 m), +180° no decrescente | km da ANTT cresce no mesmo sentido do SNV em todas as concessões; lado da pista confirma o sentido em 96% dos radares a 25-80 m do eixo |
| DNIT — PNCV | `Faixas` (`P-C-n` crescente, `P-D-n` decrescente) | Idem, SNV | 927 de 1.774 com um sentido só; lado da pista confirma em 95-100% a > 15 m do eixo |
| DER-GO | `SENTIDO`, `SRE`, `COMPLEMENT` (km) | Malha estadual da Goinfra (`MalhaEstadual_gdb`): trechos por SRE com km inicial/final, km de cada vértice pelo comprimento | Trecho desenhado do km inicial ao final em 1.002 de 1.010 radares; km do radar a 4 m da posição (mediana); lado da pista confirma em 94-100% a > 15 m |
| Artesp (concessões SP) | `Sentido` Norte/Sul/Leste/Oeste (nominal da rodovia) | Via do OSM mais perto (≤ 40 m, rede principal): mão única no sentido nominal, ou a orientação da via mais perto do ponto cardeal (≤ 60°) | Via quase perpendicular ao nominal fica vazia |
| DER-SP (rede própria) | `Faixas` (`N-1`, `S-1`, `L-1`, `O-1`) | Idem Artesp; "N-1, S-1" = os dois sentidos | — |
| OpenStreetMap | `direction` do radar | **Não usado** (`osm_pbf.USE_OSM_DIRECTION = False`). Código pronto: graus e pontos cardeais direto; `forward`/`backward` pela direção do way no nó | 1.587 dos 9.888 radares do OSM têm `direction`. Nas BRs (radar a > 15 m do eixo) o lado da pista confirma o sentido em só 38% dos 88 casos (DNIT 97%, ANTT 89%, DER-GO 90%, DER-SP 78%); a < 40 m de um radar oficial com sentido, 1 em cada 3 aponta o contrário. Muitos mapeadores marcam para onde a câmera olha |
| ANTT — Sinalização (placas) | `sentido` Crescente/Decrescente | SNV, como os radares | Por km de rodovia: 4.845 km com o mesmo limite nos dois sentidos (ponto sem sentido), 3.690 km com limites diferentes (antes descartados; agora um ponto por sentido), 1.871 km com placa de um sentido só (antes valia para os dois; agora só para o sentido dela) |

A medição do "lado da pista": no Brasil se anda pela direita, então num trecho de pista dupla
o radar do sentido crescente fica à direita do eixo (olhando para onde o km cresce). Perto do
eixo (< 15 m) o próprio eixo do SNV/Goinfra é impreciso e a concordância cai para 63-86%; com o
radar claramente numa das pistas ela sobe para 94-100%, o que mostra que o erro está no eixo,
não no sentido informado. O rumo em si vem da tangente da via e não depende desse deslocamento.

Sem sentido publicado: Rio (SMTR), BH, Detran-DF, DER-PE, Fortaleza, Recife, João Pessoa e os
limites do Rio e da CET-SP (trechos de rua, valem para os dois sentidos).
