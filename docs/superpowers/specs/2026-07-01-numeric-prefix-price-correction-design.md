# Numeric Prefix Price Correction Design

**Goal:** correggere automaticamente i setup con prezzi espressi nell'unita' dell'asset base quando il simbolo runtime e' un contratto con prefisso numerico exchange, come `1000PEPEUSDT`, e rifiutare il segnale se la correzione non e' verificabile in modo sicuro.

**Problem:** la chain `248` ha mostrato un mismatch di scala tra:
- prezzi del segnale (`0.00000226`, `0.00000247`, `0.00000263`);
- `mark_price` exchange per `1000PEPEUSDT` (`0.0022537`).

Il runtime ha quindi mischiato due unita' di prezzo diverse nello stesso sizing, generando qty enormi e rifiuto Bybit per `max_qty`.

## Decisione Architetturale

La correzione deve vivere nel layer `signal_enrichment`, non in `entry_gate` o nel gateway:
- il segnale viene normalizzato prima di `risk_capacity`, `plan_state`, `risk_snapshot` e comandi;
- l'audit della correzione resta associato all'enrichment;
- si evita che parser/enrichment dicano una cosa e lifecycle/gateway ne eseguano un'altra.

Nuovo modulo:
- `src/runtime_v2/signal_enrichment/price_corrections.py`

Responsabilita':
- contenitore unico per regole di correzione prezzi pilotate da config;
- funzione pubblica orchestratrice;
- helper specifici estendibili nel tempo.

## API Proposta

Funzione pubblica:
- `apply_price_corrections(signal, market_snapshot, config) -> PriceCorrectionResult`

Helper specifici iniziali:
- `_correct_numeric_prefix_contract_prices(...)`
- `_round_to_tick(...)` future-ready, non obbligatorio in questo task
- `_clamp_to_exchange_precision(...)` future-ready, non obbligatorio in questo task

`PriceCorrectionResult` deve poter rappresentare:
- segnale invariato;
- segnale corretto con audit;
- reject richiesto con `reason_code`.

Non serve introdurre una gerarchia complessa: una o due dataclass locali al modulo bastano.

## Regola `numeric_prefix`

### Attivazione

La regola NON si attiva implicitamente con `price_corrections.enabled`.

Config proposta:

```yaml
price_corrections:
  enabled: false  # Abilita il motore di correzione prezzi.
  numeric_prefix_exchange_rescale: false  # Corregge setup fuori scala per simboli tipo 1000PEPE usando il mark price exchange.
  numeric_prefix_max_mark_deviation_ratio: 0.20  # Deviazione massima ammessa tra prezzo corretto e mark price per considerare valida la riscalatura.
  reject_on_unresolved_numeric_prefix_mismatch: true  # Se il mismatch su simboli con prefisso numerico non e' correggibile in modo sicuro, blocca il segnale.
  round_to_tick: false  # Arrotonda i prezzi al tick size.
  clamp_to_exchange_precision: false  # Adatta i prezzi alla precisione exchange.
```

Didascalia:
- `enabled: false` = il modulo correzioni e' spento.
- `numeric_prefix_exchange_rescale: true` = abilita solo la correzione tipo `1000PEPE`.
- `numeric_prefix_max_mark_deviation_ratio` = soglia di confidenza rispetto al `mark_price`.
- `reject_on_unresolved_numeric_prefix_mismatch: true` = fallback di sicurezza richiesto dall'utente.

### Rilevamento

La regola si applica solo se:
- il simbolo raw ha base con prefisso numerico, es. `1000PEPEUSDT`;
- il prefisso numerico e' estraibile come fattore intero (`1000`);
- il `market_snapshot.mark_price` e' disponibile.

### Tentativo di correzione

Dato un fattore `N`:
- se i prezzi del setup sono gia' coerenti con `mark_price`, non si modifica nulla;
- altrimenti si prova una riscalatura moltiplicando tutti i prezzi del setup per `N`;
- opzionalmente si puo' anche provare `/N`, ma per questo caso il target primario e' il setup scritto nel prezzo dell'asset base e il contratto exchange espresso nel bundle `N`.

Il setup corretto e' accettato solo se:
- il primo prezzo entry corretto e' entro la soglia `numeric_prefix_max_mark_deviation_ratio` dal `mark_price`;
- l'ordine logico del setup resta valido:
  - `LONG`: `SL < entries` e TP sopra l'entry;
  - `SHORT`: `SL > entries` e TP sotto l'entry;
- tutte le entry, SL e TP restano positive.

Se nessuna variante supera i controlli:
- `BLOCK` con reason `numeric_prefix_price_mismatch_unresolved`.

## Punto di Integrazione

In `src/runtime_v2/signal_enrichment/processor.py`:
- nel path `SIGNAL`, dopo blacklist/struttura/SL/TP obbligatori;
- prima di `price_sanity`;
- prima della costruzione finale di `EnrichedSignalPayload`.

Questo garantisce che:
- `risk_capacity` legga prezzi gia' coerenti;
- `entry_gate` e il piano esecutivo usino lo stesso setup corretto.

## Audit

La correzione deve lasciare traccia in `enrichment_log` con:
- `check="numeric_prefix_exchange_rescale"`
- fattore applicato;
- `mark_price`;
- prezzi originali entry/SL/TP;
- prezzi corretti.

Se il segnale viene rifiutato per mismatch non risolvibile:
- loggare un record con `check="numeric_prefix_exchange_rescale_rejected"`.

## Testing

Coverage minima:
- simbolo `1000PEPEUSDT` con prezzi asset-base, `mark_price` x1000 -> `PASS` e prezzi corretti;
- stesso simbolo con prezzi gia' coerenti -> `PASS`, nessuna correzione;
- simbolo con prefisso numerico ma `mark_price` assente -> nessuna correzione automatica; se il setup appare fuori scala e `reject_on_unresolved_numeric_prefix_mismatch=true`, `BLOCK`;
- simbolo con prefisso numerico ma nessuna variante coerente -> `BLOCK`;
- config disabilitata -> comportamento invariato.

## Impatto

Layer toccati:
- `signal_enrichment` config models/loader;
- `signal_enrichment` processor;
- nuovi test di processor;
- possibile test unitario del nuovo modulo helper.

Nessuna migrazione DB richiesta.
