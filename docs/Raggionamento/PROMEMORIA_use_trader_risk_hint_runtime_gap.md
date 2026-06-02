# Promemoria - `use_trader_risk_hint` attivo in config ma non applicato nel runtime trade

## Contesto

Nel file:

```text
C:\TeleSignalBot\config\operation_config.yaml
```

e presente:

```yaml
use_trader_risk_hint: true
```

con commento:

```text
true = usa eventuale hint rischio dal segnale/trader
```

La verifica serve a chiarire se questo flag influenzi davvero il sizing/risk
del trade nel runtime v2.

---

## Esito della verifica

Esito netto:

```text
oggi il runtime v2 NON usa il risk_hint del segnale per calcolare il rischio del trade
```

Il flag viene:

```text
- letto nella config
- portato nel policy_snapshot
```

ma non viene consumato nel path che decide:

```text
- risk_amount
- size_usdt
- qty per leg
- payload finali verso execution gateway
```

---

## Dove il sistema supporta davvero `risk_hint`

### 1. Parser / contratto canonical

Il parser supporta `risk_hint` nel payload del segnale.

In pratica il dato esiste a livello di modello canonical:

```text
SignalFields.risk_hint
```

e i test parser verificano che venga estratto.

Quindi:

```text
estrazione parser = presente
```

---

## Dove il flag runtime entra davvero

### 2. Config runtime

Il runtime v2 ha il campo:

```text
RiskConfig.use_trader_risk_hint
```

e il loader costruisce `RiskConfig` dalla config effettiva trader.

Quindi:

```text
flag config = presente e caricato
```

Nota importante:

```text
il loader runtime v2 legge gli override da config/traders/<trader_id>.yaml
non da config/trader_rules/<trader_id>.yaml
```

Quindi eventuali `use_trader_risk_hint: false` dentro `config/trader_rules/`
non sono la fonte corretta per questo path runtime.

---

## Punto in cui ci si aspetterebbe l'effetto

### 3. Signal enrichment

Il `SignalEnrichmentProcessor`:

```text
- valida il segnale
- costruisce enriched_signal
- salva policy_snapshot
```

ma non applica alcuna logica che traduca:

```text
signal.risk_hint -> risk.risk_pct_of_capital effettivo
```

Quindi il segnale passa avanti senza che il suo hint rischio modifichi il budget.

---

## Punto reale dove nasce il budget rischio

### 4. Risk engine

Il budget rischio del trade viene deciso nel `RiskCapacityEngine`.

La logica osservata e:

```text
se mode == risk_usdt_fixed:
    risk_amount = risk.risk_usdt_fixed

altrimenti:
    risk_amount = capital * risk.risk_pct_of_capital / 100
```

Questa e la parte decisiva.

Non c'e un ramo del tipo:

```text
if use_trader_risk_hint and signal.risk_hint is present:
    usa il valore dal segnale
```

Quindi:

```text
il rischio usato e solo quello da config runtime
```

---

## Effetto downstream

### 5. Entry gate e execution gateway

Una volta deciso `risk_amount`, il resto della chain usa solo quel valore:

```text
- allocazione rischio per leg
- risk_snapshot_json
- comandi entry
- deferred market qty
```

Nel caso `deferred_market`, anche il gateway exchange ricalcola la qty da:

```text
risk_amount / distanza_stop
```

ma quel `risk_amount` arriva gia dal risk engine, non dal `risk_hint`.

---

## Conclusione pratica

Oggi il significato reale di:

```yaml
use_trader_risk_hint: true
```

e:

```text
flag dichiarato nella config
ma non ancora implementato nel motore runtime che esegue il trade
```

Quindi il comportamento effettivo attuale e:

```text
il trade usa il rischio configurato nel runtime
non il rischio rilevato nel messaggio del trader
```

---

## Implicazione architetturale

Questo non e un bug parser.

E un gap di integrazione tra:

```text
layer parsing / canonical message
e
layer runtime risk sizing
```

In altre parole:

```text
il dato esiste
il flag esiste
ma manca il collegamento semantico nel layer che possiede la decisione rischio
```

---

## Direzione corretta per il fix

Se si vuole che `use_trader_risk_hint` abbia effetto reale, il punto corretto da
toccare e il layer che decide il budget rischio, cioe:

```text
RiskCapacityEngine
```

non il gateway exchange e non il parser.

La decisione da chiarire prima dell'implementazione e:

```text
come mappare il risk_hint del segnale sul budget runtime
```

Casi da definire:

1.

```text
risk_hint.value singolo
```

2.

```text
risk_hint range min/max
```

3.

```text
precedenza tra hard cap account, cap trader e hint del segnale
```

4.

```text
se l'hint riduce soltanto il rischio configurato
oppure puo anche aumentarlo
```

Scelta consigliata:

```text
il risk_hint del segnale puo solo ridurre il rischio base di config
mai aumentarlo oltre i cap runtime
```

---

## Regola pratica da preservare

Finche il gap non viene chiuso, bisogna assumere:

```text
`use_trader_risk_hint: true` non e una garanzia di comportamento attivo
```

Quindi qualsiasi analisi operativa o log futura deve distinguere tra:

```text
flag configurato
vs
risk hint effettivamente applicato al sizing
```

---

## Sintesi finale

Il sistema attuale:

```text
sa estrarre il risk_hint
sa configurare use_trader_risk_hint
ma non usa quel hint nella decisione finale del rischio trade
```

Il gap reale e:

```text
missing integration between canonical signal risk hint and runtime risk sizing
```
