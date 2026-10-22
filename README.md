# Local Alt text

[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](https://www.gnu.org/licenses/gpl-3.0)

> **Disclaimer:** 100% vibecoded by deepseek chat with copy/paste and review.
>
> **Proposta de viabilidade:** Geração de ALT text de imagens localmente com baixa demanda de VRAM.
>
> A versão atual parece muito consistente e foi comparada lado a lado com o prompt idêntico pago da DeepSeek via API.
>
> Baseado nas tabelas de Ansel Adams, pedi para fazer histogramas determinísticos; formulei perguntas estratégicas para o OpenDecision, restringindo o cenário que o Qwen recebe e facilitando o output desejado.
>
> Testado em uma placa com `qwen2.5vl:7b` em 6 GB de VRAM. Funciona também com o `qwen2.5vl:3b`, com um pouco de prejuízo na leitura de textos na imagem.

---

Geração local de ALT para imagens via Ollama. Foco: acessibilidade.

Pipeline em três estágios: heurísticas determinísticas → OpenDecision
(classificação visual via SigLIP2) → geração via Qwen 7b. Config
declarativo, sem fallbacks ocultos.


## Estado

v1.0.0 — funcional e validado em uso real.
17 imagens em ~190s (warm). Primeira imagem ~55s (cold Qwen).
Chamadas consecutivas warm (~7s) graças a `keep_alive`.

## Requisitos

  Python 3.11+
  Ollama server (https://ollama.com) com qwen2.5vl:7b
  OpenDecision instalado e disponível no PATH (`opendecision --version`)
  Calibração privada: exemplos/ + referencias.json (opcional, só
  necessário para `comparar.py --examples`)

## Instalação

  # Opção rápida (script)
  ./install.sh

  # 1. Ollama server (uma vez, fora do projeto)
  ollama pull qwen2.5vl:7b

  # 2. App (venv pequeno, só deps do pipeline)
  python3 -m venv .venv
  source .venv/bin/activate
  pip install ollama Pillow httpx

  # 3. OpenDecision isolado (uv tool ou pipx; ~3 GB fora do .venv)
  uv tool install 'OpenDecision[images]'
  # alternativa sem uv: pipx install 'OpenDecision[images]'

  # 4. Verificar
  opendecision --version        # deve responder 0.2.0
  python3 alt.py --dry-run      # deve imprimir "config: 10 routes"

## Comandos

  python3 alt.py IMG.jpg                 ALT de uma imagem
  python3 alt.py --batch DIR/            ALT de um diretório (2 fases)
  python3 comparar.py --examples         ALT vs REFs (exige exemplos/ + referencias.json locais)
  python3 limpar.py --all                seleciona downloads/ + exemplos/ (menu: [o]/[a]/[b])

Flags úteis em `alt.py`:

  --force         reprocessa mesmo se sidecar existe
  --no-od         só heurística (debug, rápido)
  --no-metrics    não escreve em metricas.csv
  --profile       timings por estágio
  --dry-run       config + status + manifest, sem processar

## Pipeline (uma imagem)

  1. Heurísticas (extract_heuristics.py) — ~0.1s, sem GPU
     Decide ~2/3 das imagens via EXIF, ruído, densidade de bordas,
     paleta e luminância.

  2. OpenDecision (extract_facts.py) — só se heurística foi indecisa
     Consulta contrato <img>.od.json (cache);
     se ausente/stale, chama SigLIP2 (~12s cold, ~0.2s warm) e regrava.
     Duas perguntas numa chamada: tipo visual + ação.

  3. Geração (Qwen 7b) — ~7s warm / ~55s cold
     Prompt da rota + heurística + tipo_visual + ação.
     Trunca em max_alt_chars. Grava sidecar com sha256.

## Pipeline (batch)

  Fase 1 — classificação (heurística + contratos OD ou inline).
  Fase 2 — geração (Qwen carregado uma vez, gera todas warm).

  Warm: ~190s / 17 imagens. Cold: ~200s.

## Contratos OD

  <img>.od.json — ao lado da imagem, ignorado pelo git.

  {
    "image_sha256": "<64 hex>",
    "model": "google/siglip2-base-patch16-224",
    "ts": "<iso>",
    "visual_type": { "choice", "probabilities", "confidence" },
    "action":      { "choice", "probabilities", "confidence" }
  }

  Só existe para imagens ambíguas. Imagens decididas pela heurística
  não têm contrato — a heurística é rápida, não precisa de cache.
  Para reavaliar com vocabulário novo: `limpar.py --all` + `--batch`.

## Config

  Toda a configuração em config.json. Schema estrito — chaves ausentes
  ou com tipo errado falham no startup (validate_config).

  Seções:
    general      paths, thresholds, LLMs, judge, keep_alive
    heuristics   thresholds de classificação determinística
    routes       um prompt/modelo por tipo visual

  Detalhes de cada campo em VARIABLES.txt.

## Validação (opcional)

`collect.py` coleta pares (imagem, ALT de referência) a partir de um
export `history.txt` do Gajim. Use quando quiser comparar seu pipeline
local com um "gold standard" produzido por um bot de API de alta
capacidade (GPT-4V, Claude, Gemini etc.).

A regex do marcador fica em `config.json`, em `general.gajim_alt_marker`.
Adapte ao formato do seu bot. Se não casar, `collect.py` retorna
0 pares com um aviso.

    python3 collect.py ~/path/to/history.txt --output coletados.json
    python3 comparar.py                       # sidecar vs referência
    python3 report.py compare                 # agrega

A saída (`coletados.json`) contém dados de chat (URLs, ALTs de
referência, timestamps). **Nunca commite.** O `.gitignore` exclui.

Este fluxo é para um único usuário nos próprios dados. Não é um
parser genérico de chat — a regex é um template que você adapta.

## Design

  - altlib/ — pacote compartilhado (paths, config, log, llm, image,
    contract, manifest, routing, status).
  - Cada estágio é um subprocesso com contrato JSON. Sem import direto
    entre scripts de topo — sem bypass.
  - runtime.py centraliza ciclo de vida dos LLMs (VRAM-safe).
  - manifest.json — histórico das últimas execuções.
  - logs/execucao.jsonl — stages + chamadas LLM.
  - logs/od_observacoes.jsonl — distribuições do SigLIP2 (tipo visual).

## Limitações conhecidas

  - Qwen 7b e SigLIP2 não coexistem em 6 GB VRAM.
    OD roda com Qwen descarregado; contrato evita reload.
  - Juiz text-only (comparar.py) não detecta erro semântico.
  - Viés render_3d/pixel_art em fotos com superfícies lisas
    (~3/21 casos). Rota errada, ALT correto.
  - action só é logada no contrato; analisar_od.py mostra apenas tipo.
  - Editar vocabulario.json ou acoes.json não invalida contratos.
    Rode `limpar.py --all` para forçar re-classificação.

## Estrutura

  install.sh                instalação automatizada (venv + deps + OD + modelo)
  alt.py                    pipeline + CLI
  runtime.py                LLMControl + CLI (status/check/ensure/stop)
  limpar.py                 remove contratos/ALTs (menu)
  comparar.py               sidecar vs REFs + juiz text-only
  report.py                 sumários CSV
  analisar_od.py            análise do od_observacoes.jsonl
  collect.py                extrai pares do history.txt do Gajim
  extract_heuristics.py     classificador PIL (heurísticas)
  extract_facts.py          cliente OpenDecision (SigLIP2)
  altlib/                   pacote compartilhado

  config.json               config principal
  vocabulario.json          tipo visual (inglês, SigLIP2)
  acoes.json                classes de ação (inglês, SigLIP2)
  referencias.json          17 ALTs de referência (calibração)
  VARIABLES.txt             referência completa

  exemplos/                 calibração (privada, não versionada)
  downloads/                capturas reais (Gajim)
  logs/                     execucao.jsonl, od_observacoes.jsonl

  Traga seu próprio exemplos/ e referencias.json. Ambos são
  privados (não versionados) — o repositório não inclui imagens
  nem ALTs de referência.

## Verificação

  python3 alt.py --dry-run         config + status + manifest
  python3 runtime.py status        VRAM, modelos, último batch
  python3 analisar_od.py           distribuição do SigLIP2

Detalhes de cada campo em VARIABLES.txt.

## Licença

Este programa é software livre: você pode redistribuí-lo e/ou modificá-lo
sob os termos da **GNU General Public License** conforme publicada pela
Free Software Foundation, seja a versão 3 da Licença, ou (a seu critério)
qualquer versão posterior.

Veja o arquivo [LICENSE](LICENSE) para o texto completo.