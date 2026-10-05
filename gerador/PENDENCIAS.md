# Pendências do Pardal

Situação em 2026-09-30, com as fontes travadas conferidas de novo em 2026-10-05. O que não foi feito ainda e por quê.

## Manutenção

| Item | Situação |
|---|---|
| **Zerar o histórico do git de tempos em tempos** | Combinado. Cada geração acrescenta dezenas de MB ao histórico e só a versão mais recente interessa a quem baixa. Quando o repositório passar de ~1 GB: criar um branch órfão com o estado atual, forçar o push no `main` e apagar o branch antigo. |

## Melhorias de dados

| Item | Por que ainda não | O que destrava |
|---|---|---|
| **Radares estaduais da Bahia** | A Seinfra-BA publicava um KML com os radares das rodovias estaduais, mas a página está suspensa pelo período eleitoral (outubro/2026). Conferido em 2026-10-05, depois do primeiro turno: continua redirecionando para "conteúdo temporariamente indisponível". | Tentar de novo a partir de novembro (depois do segundo turno, 2026-10-25): `ba.gov.br/infraestrutura/51/equipamentos-de-fiscalizacao-eletronica-das-rodovias-baianas`. Com o KML, é uma fonte nova no padrão de `sources/`. |
| **Cadastro atual do DF** | O bloqueio do portal era do endereço antigo: o portal novo tem API aberta (`/o/dados-abertos/v1.0/datasets/<slug>`). Mas o único conjunto do Detran-DF sobre fiscalização traz só a contagem de equipamentos por região administrativa, sem coordenada; nenhum dos 180 conjuntos do portal tem a localização. Conferido em 2026-10-05: os mesmos 180 conjuntos; o do Detran-DF foi atualizado em 2026-07-01 ("Ano 2025") e segue só com a contagem (475 cruzamentos, 149 controladores de velocidade…). Segue a camada ArcGIS do Detran-DF, de 2019. | Pedido por Lei de Acesso à Informação ao Detran-DF e ao DER-DF (o DER-DF publica no DERGeo a malha com `velocidade_max`, mas não os radares). |
| **Radares do Inmetro sem coordenada** | ~8.900 medidores válidos têm só endereço urbano (7.311 locais distintos) e ~2.500 só "rodovia estadual + km". A conversão de km (SNV e marcos do OSM) foi medida e é imprecisa demais para criar radar. Para os endereços, o CNEFE do IBGE (Censo 2022) serve de geocodificador e já é fonte do gerador (`inmetro_addresses.py`): "rua + número" e cruzamento, 2.938 locais localizados, 1.714 radares novos (ver `FONTES.md`). Ficam de fora os outros jeitos de escrever o endereço: ponto de referência ("em frente a…"), setor de Brasília e quadra/lote de Goiás, cerca de um terço dos locais. | Para os endereços que faltam: ler quadra e lote nos complementos do CNEFE (Goiás) e as quadras de Brasília. Para "rodovia estadual + km": a base de quilometragem (SRE) de cada estado. |
| **Radares do Rio sem número de porta** | 468 dos 1.083 itens da lista da SMTR/CET-Rio têm endereço sem número ("acesso ao mergulhão…"); geocodificar pelo nome da rua cairia no meio dela. | Geocodificar por cruzamento ("RUA X com RUA Y") pelo geocodificador da prefeitura, ou casar com radares do OSM. |
| **Listas de DER-MG, DER-PR, DAER-RS, DER-ES, DER-RJ** | Só "rodovia + km" ou PDF, sem coordenada. | Mesma limitação da quilometragem estadual. |
| **Outras capitais** (Curitiba, Porto Alegre, Salvador, Goiânia, Manaus…) | Publicavam só mapa na tela ou PDF sem coordenada. Conferido em 2026-10-05: **Curitiba abriu**, a página de fiscalização eletrônica da Setran (`transito.curitiba.pr.gov.br/fiscalizacaoeletronica`) lista 309 pontos, cada um com coordenada, identificação ("RADAR AR-15"), o que fiscaliza (velocidade, parada na faixa, avanço de sinal) e o limite; vira fonte nova. **Porto Alegre**: um PDF de locais e limites e dois relatórios Power BI públicos do ObservaMob da EPTC (o gerador já lê Power BI na CET-SP); falta ver se o relatório traz coordenada. Goiânia, Salvador e Manaus: nada aberto achado (portais CKAN sem conjunto de radares ou fora do ar). | Curitiba: fonte nova no padrão de `sources/`. Porto Alegre: ler o relatório. As outras: arquivo aberto com coordenada ou geocodificador da própria prefeitura (como o do Rio). |
| **Limites como linhas** | Hoje o limite é uma sequência de pontos. Para mapas, o ideal é o trecho da via como linha com o valor. | Guardar a geometria das vias no gerador; arquivos bem maiores, provavelmente só por estado. |
| **Sentido dos radares urbanos e estaduais sem campo de sentido** (Rio, BH, Detran-DF, DER-PE, Fortaleza, Recife, João Pessoa) | As bases não informam o sentido (ver "Sentido" em `FONTES.md`). Em cidade, radar costuma ficar num poste de um lado da rua, mas a coordenada publicada não é precisa o bastante para deduzir o lado. | Um campo de sentido nas bases, ou casar com radares do OSM que tenham `direction`. |
| **Sentido de SP e da CET em via de mão dupla** | Só sai sentido confirmado pela posição (radar sobre pista de mão única). A tentativa pela quilometragem (malha do DER-SP + planilha da Artesp) mostrou que o próprio sentido nominal erra em ~13-19% dos radares conferíveis (ver `FONTES.md`). | Uma fonte com o rumo ou a pista de cada equipamento (não o sentido nominal da rodovia), ou conferência em campo. |
| **Radares da CET pela API do Power BI** | A fonte é um relatório Power BI público, lido pela API que o próprio relatório usa (não documentada). Se a Microsoft ou a CET mudarem o relatório, a fonte falha (o build registra e o publicar barra). | Pedir à CET a publicação da tabela em formato aberto (CSV no portal de dados abertos da prefeitura). |
| **Limites estimados nas estradas rurais** (`unclassified`) | Eram 75% do volume dos estimados (em Goiás, 135 mil km) e é onde a regra por tipo de via mais erra (40/60 km/h em estrada de terra). | Uma estimativa mais confiável (por exemplo, usando o tipo de superfície) e um formato que caiba no limite de 100 MB por arquivo. |

## Publicação

Os dados e este gerador ficam em github.com/Murkiriel/pardal. O código é MIT (`LICENSE`
desta pasta); os dados, ODbL 1.0. `data/` (downloads das fontes e resultados intermediários)
fica fora do repositório (`.gitignore` da raiz). Commits no repositório usam o e-mail anônimo
do GitHub (`…@users.noreply.github.com`), nunca o pessoal.
