# V4 Crypto Monitor — v0.3.1

A v0.3.1 mantém o motor da v0.3 e torna a atualização de dados mais resiliente. A v0.3 transforma o dashboard em um **motor semanal com dados públicos atualizáveis**, mantendo o replay histórico da v0.2.

## O que a v0.3 faz

### V4 Core — operacional
- baixa candles diários e volume cotado dos pares Spot/USDT da Binance;
- mantém cache local incremental;
- mantém uma cópia local da série diária do S&P 500 (`SP500`) do FRED;
- calcula o regime mensal;
- reconstrói o universo estrutural mensal;
- calcula os sinais V4 nas sextas-feiras;
- aplica a regra de **2 confirmações consecutivas**;
- gera os pesos-alvo da V4 Core;
- compara com a semana anterior e lista as ações sugeridas;
- **não envia ordens** e não acessa conta/saldo da corretora.

### V4-O — monitoramento
- roda em paralelo somente quando o regime é Risk On;
- usa a arquitetura congelada de oportunidade: pivô + ADX/AO + MACD2 + ranking estrutural balanceado;
- mostra no máximo um candidato;
- nunca entra na lista de ordens da V4 Core.

### Replay histórico
Continua disponível no mesmo app e usa os resultados congelados do backtest até 31/08/2026.

---

## Regras implementadas na V4 Core

### Regime mensal
O sinal é calculado com o último mês completo:

1. `BTC 30d > S&P 500 30d` → **Risk On**;
2. caso contrário, se `BTC 90d > 0` → **Neutral**;
3. caso contrário → **Defensive**.

Essa regra de três estados reproduziu os regimes históricos V3/V4 no conjunto usado na pesquisa.

### Universo estrutural
Para altcoins:
- idade ≥ 48 meses;
- presença no Top 30 de liquidez ≥ 6 dos últimos 12 meses;
- sequência corrente no Top 30 ≥ 3 meses;
- score de solidez ≥ 0,55.

Score de solidez:
- idade: 25%;
- persistência: 35%;
- qualidade do rank: 25%;
- continuidade: 15%.

BTC e ETH não passam por esse filtro; são o núcleo da carteira em Risk On.

### Sinais V4
A seleção une dois setups.

**Continuação**
- consenso de tendência ≥ 3 de 5 votos;
- força relativa persistente;
- estrutura HH/HL;
- tendência por médias;
- ADX/+DI;
- MACD bullish.

**Pivot correção + aceleração**
- correção recente: preço abaixo da EMA20 em algum dos últimos 10 dias ou DD20 ≤ -8%;
- recuperação: preço > EMA10;
- aceleração ≥ 2 de 4: AO, MACD histograma, RSI e estocástico;
- pelo menos 1 voto de tendência;
- extensão ≤ 2 ATR da EMA20.

A união dos setups gera até 3 altcoins, ordenadas por confiança do setup, solidez e liquidez.

### Confirmação semanal
- na virada do mês entra a seleção-base mensal;
- a seleção é recalculada toda sexta-feira;
- uma troca intramês só é confirmada quando a mesma lista ordenada aparece em **duas sextas consecutivas**.

### Pesos
**Risk On:** pesos iguais entre BTC, ETH e as altcoins confirmadas.

**Neutral:**
- BTC 10%;
- ETH 10%;
- USDT 80%.

**Defensive:**
- USDT 100%.

Os testes finais de sensibilidade mantiveram pesos iguais como benchmark operacional mais forte frente a inverse-vol e 30% BTC + 20% ETH.

---

## Fontes de dados

### Binance
A v0.3 usa endpoints públicos de mercado Spot. Não precisa de API key.

Na primeira carga o app:
1. descobre os pares USDT mais líquidos;
2. baixa aproximadamente 430 dias de candles diários;
3. consulta a primeira data disponível de cada ativo para medir idade;
4. salva tudo em `data/cache/`.

As atualizações seguintes são incrementais.

### S&P 500
O app usa o CSV público da série `SP500` do FRED, mas **não consulta o FRED a cada atualização**.

Como o regime macro da V4 é fixado pelo último mês completo, a cópia em `data/cache/sp500.csv` é reutilizada durante todo o mês. O FRED só é consultado quando a cópia local ainda não cobre o fechamento do mês anterior. A chamada usa tentativas automáticas, timeout maior e uma janela reduzida de histórico. Se o FRED estiver temporariamente indisponível e a cópia local já for suficiente, o app continua normalmente com o cache.

Não é necessário criar chave de API nesta versão.

---

## Instalação no Windows

Na pasta do projeto:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Depois:

```powershell
.\run_app.ps1
```

ou:

```powershell
python -m streamlit run app.py --server.address 127.0.0.1 --server.port 8501 --server.headless true
```

Abra:

```text
http://127.0.0.1:8501
```

---

## Primeira execução

1. Escolha **Ao vivo (v0.3)** na barra lateral.
2. Clique em **Atualizar dados**.
3. Aguarde a carga inicial. Ela faz muitas consultas públicas e pode levar alguns minutos.
4. Quando o cache ficar pronto, o painel exibirá a revisão mais recente.
5. Use **Salvar snapshot desta revisão** para iniciar o histórico prospectivo congelado.

Nas semanas seguintes, **Atualizar dados** acrescenta somente o que estiver faltando na Binance. O S&P 500 é reaproveitado localmente e normalmente só precisa de uma nova consulta uma vez por mês.

---

## Estrutura do projeto

```text
v4_dashboard_v0_3/
├── app.py
├── config.py
├── requirements.txt
├── run_app.ps1
├── data/
│   ├── live_data.py
│   ├── v4_core_events.csv
│   ├── v4_opportunities.csv
│   ├── v4_performance_weekly.csv
│   └── cache/                  # criado localmente
├── engine/
│   ├── core.py                 # replay histórico
│   ├── live.py                 # motor ao vivo
│   ├── indicators.py
│   ├── models.py
│   └── rebalance.py
└── tests/
```

---

## Diferença importante entre replay e motor ao vivo

O **Replay histórico** usa os eventos exatamente congelados nos backtests.

O **motor ao vivo** calcula novamente o sinal a partir dos dados públicos disponíveis no cache. Ele é a implementação prospectiva da metodologia documentada, não uma tentativa de recalibrar o passado.

A primeira carga começa com os pares atualmente mais líquidos e, a partir daí, o cache conserva os ativos já observados. Isso permite que o histórico de liquidez do universo cresça prospectivamente sem apagar moedas que depois saiam do grupo mais líquido.

---

## Governança recomendada

- não alterar parâmetros da V4 Core com base nos resultados das próximas semanas;
- salvar um snapshot a cada revisão;
- manter V4-O apenas em monitoramento;
- registrar qualquer futura mudança de regra como nova versão do modelo;
- antes de automatizar ordens, validar por um período prospectivo suficiente e adicionar uma camada separada de execução/segurança.


## v0.3.2 — conectividade e cache mensal
- Binance e S&P 500 possuem botões independentes.
- Botões ficam desabilitados quando a respectiva base está suficientemente atualizada.
- A interface mostra `Dados atualizados até DD/MM/AAAA — nenhuma ação necessária`.
- O pacote traz um bootstrap mínimo do S&P 500 para não depender de rede na primeira execução.
- Atualização mensal do S&P 500: FRED primário, Stooq fallback; falha das duas fontes preserva o cache local.
- Se uma virada de mês ocorrer sem acesso às fontes, o motor carrega adiante a última referência macro válida e sinaliza o estado no Diagnóstico.
