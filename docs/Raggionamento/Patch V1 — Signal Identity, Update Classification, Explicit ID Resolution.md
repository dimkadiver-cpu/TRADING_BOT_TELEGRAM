# PRD — Patch minima per Signal Identity e Target Resolution

## 1. Titolo

**Patch V1 — Signal Identity, Update Classification, Explicit ID Resolution**

---

## 2. Stato

Draft

---

## 3. Scopo

Questo PRD definisce una patch migliorativa minima, senza rifare la struttura attuale del sistema.

La patch copre solo tre interventi:

```text
1. salvare identità minima della chain
2. evitare che un update venga classificato come SIGNAL parziale
3. correggere la risoluzione degli explicit_id / Signal ID
```

Sono esclusi da questo PRD:

```text
4. blindatura multi-chain/global scope
5. MARKET_NOW con override SL/risk
```

Questi saranno trattati in PRD separati.

---

## 4. Contesto

Il sistema attuale ha già:

```text
- parser_v2
- TargetHints
- explicit_ids
- reply_to_message_id
- telegram links
- target_action_groups
- lifecycle update
- TradeChain runtime
```

Il problema è che alcuni update non vengono collegati in modo affidabile al segnale originale.

Esempio segnale originale:

```text
[trader #c] Signal ID: #c4

#AAVEUSDT LONG

Вход: 61 лимитка

TP: 69,73

SL: 59,57

Риск на сделку 1%
```

Esempio update:

```text
[trader #c] Signal ID: #c4

#AAVEUSDT LONG

Торговая идея изменилась

Вход по рынку

SL 60.73

Риск на сделку 1%
```

Il sistema dovrebbe capire:

```text
Signal ID #c4
→ chain originale di trader_c
→ update applicabile alla chain corretta
```

Oggi questo collegamento è fragile.

---

## 5. Problemi da risolvere

## 5.1 Signal ID non viene salvato come identità persistente

Il messaggio originale può contenere:

```text
Signal ID: #c4
```

Ma la trade chain attuale non salva stabilmente:

```text
external_signal_id = c4
```

Quindi quando arriva un update con `Signal ID #c4`, il runtime non ha una chiave corretta per trovare la chain.

---

## 5.2 Update con SL/risk può diventare SIGNAL parziale

L’update:

```text
Signal ID: #c4
Вход по рынку
SL 60.73
Risk 1%
```

può essere interpretato come nuovo `SIGNAL` incompleto, perché contiene campi da segnale:

```text
- symbol
- side
- SL
- risk
```

Questo è sbagliato.

Deve essere classificato come `UPDATE`.

---

## 5.3 Explicit ID viene confrontato con ID interno sbagliato

Oggi `explicit_id = c4` rischia di essere confrontato con:

```text
canonical_message_id
```

ma `canonical_message_id` è un ID interno numerico.

Corretto:

```text
explicit_id c4
→ external_signal_id c4
```

---

## 6. Obiettivi

La patch deve garantire che:

```text
1. ogni nuovo signal accettato salvi external_signal_id, se presente
2. ogni chain salvi anche telegram_message_id/source minimi
3. un update con target + intent update venga classificato come UPDATE
4. explicit_id venga risolto contro external_signal_id
5. se il target non è risolvibile, l’update vada in review
```

---

## 7. Non-obiettivi

Fuori scope:

```text
- nuovo contratto PATCH_SIGNAL_PLAN
- merge atomico entry + SL + risk
- ricalcolo size con nuovo SL
- gestione avanzata multi-chain/global scope
- refactor completo parser
- refactor completo lifecycle
- nuovo sistema manual review
```

---

# 8. Proposta 1 — Persistenza identità minima della chain

## 8.1 Descrizione

Aggiungere alla `ops_trade_chains` alcuni campi minimi per rendere identificabile il segnale originale.

Campi richiesti:

```text
telegram_message_id
external_signal_id
source_chat_id
source_topic_id
```

`trader_id` esiste già e resta parte della chiave logica.

---

## 8.2 Migrazione DB

```sql
ALTER TABLE ops_trade_chains
ADD COLUMN telegram_message_id INTEGER;

ALTER TABLE ops_trade_chains
ADD COLUMN external_signal_id TEXT;

ALTER TABLE ops_trade_chains
ADD COLUMN source_chat_id TEXT;

ALTER TABLE ops_trade_chains
ADD COLUMN source_topic_id INTEGER;
```

Indice per Signal ID:

```sql
CREATE INDEX IF NOT EXISTS idx_otc_signal_identity
ON ops_trade_chains(
    trader_id,
    source_chat_id,
    source_topic_id,
    external_signal_id
)
WHERE external_signal_id IS NOT NULL;
```

Indice per Telegram message id:

```sql
CREATE INDEX IF NOT EXISTS idx_otc_telegram_identity
ON ops_trade_chains(
    trader_id,
    source_chat_id,
    telegram_message_id
)
WHERE telegram_message_id IS NOT NULL;
```

---

## 8.3 Regole di normalizzazione Signal ID

Input:

```text
Signal ID: #c4
Signal ID:#C4
#c4
#C4
```

Output:

```text
c4
```

Regole:

```text
- rimuovere #
- trim spazi
- lowercase
```

---

## 8.4 Salvataggio in fase di creazione chain

Quando `process_signal()` crea una nuova trade chain, deve salvare:

```yaml
trade_chain:
  trader_id: trader_c
  telegram_message_id: 1001
  external_signal_id: c4
  source_chat_id: "-100..."
  source_topic_id: 123
```

Se `external_signal_id` non è presente:

```yaml
external_signal_id: null
```

---

## 8.5 Criteri di accettazione

La proposta 1 è completata quando:

```text
1. la migrazione DB viene applicata senza rompere chain esistenti
2. i nuovi signal salvano telegram_message_id
3. i nuovi signal salvano source_chat_id/source_topic_id se disponibili
4. i nuovi signal salvano external_signal_id se presente
5. external_signal_id viene normalizzato in modo stabile
```

---

# 9. Proposta 2 — Classificazione update prima di SIGNAL parziale

## 9.1 Descrizione

Correggere la classificazione parser per impedire che update mirati vengano classificati come nuovi `SIGNAL` parziali.

Problema attuale:

```text
SL 60.73
→ SignalExtractor crea SignalDraft incompleto
→ ClassificationResolver sceglie SIGNAL
→ intent update scartati
```

Comportamento desiderato:

```text
target + update intent + campi parziali
→ UPDATE
```

---

## 9.2 Regola di classificazione

Prima di assegnare `primary_class = SIGNAL`, verificare se il messaggio sembra un update mirato.

Condizione minima:

```text
has_update_intent = true
AND
has_target_hint = true
```

Dove `has_update_intent` include almeno:

```text
- MODIFY_ENTRY
- MOVE_STOP
- MOVE_STOP_TO_BE
- CANCEL_PENDING
- CLOSE_FULL
- CLOSE_PARTIAL
- MODIFY_TARGETS
- INVALIDATE_SETUP
```

Dove `has_target_hint` include almeno:

```text
- reply_to_message_id
- telegram_message_ids
- telegram_links
- explicit_ids
- symbols
- scope_hint != UNKNOWN
```

---

## 9.3 Pseudo-codice

```python
def resolve(signal, intents, target_hints):
    if signal is not None:
        if _looks_like_targeted_update(intents, target_hints):
            return ClassificationResult(
                primary_class="UPDATE",
                parse_status="PARSED",
                warnings=["signal_like_update_forced_to_update"],
            )

        return ClassificationResult(
            primary_class="SIGNAL",
            parse_status=_signal_parse_status(signal),
            warnings=[],
        )

    ...
```

Helper:

```python
def _looks_like_targeted_update(intents, target_hints):
    has_update_intent = any(
        intent.type in UPDATE_INTENTS
        for intent in intents
    )

    has_target_hint = (
        target_hints is not None
        and (
            target_hints.reply_to_message_id is not None
            or bool(target_hints.telegram_message_ids)
            or bool(target_hints.telegram_links)
            or bool(target_hints.explicit_ids)
            or bool(target_hints.symbols)
            or target_hints.scope_hint != "UNKNOWN"
        )
    )

    return has_update_intent and has_target_hint
```

---

## 9.4 Esempio

Input:

```text
Signal ID: #c4
#AAVEUSDT LONG
Вход по рынку
SL 60.73
Risk 1%
```

Output atteso:

```yaml
primary_class: UPDATE
parse_status: PARSED
warnings:
  - signal_like_update_forced_to_update
target_hints:
  explicit_ids:
    - c4
  symbols:
    - AAVEUSDT
intents:
  - MODIFY_ENTRY
```

Output non accettabile:

```yaml
primary_class: SIGNAL
parse_status: PARTIAL
```

---

## 9.5 Criteri di accettazione

La proposta 2 è completata quando:

```text
1. update con Signal ID + MODIFY_ENTRY + SL viene classificato come UPDATE
2. update con reply + MODIFY_ENTRY + SL viene classificato come UPDATE
3. update con link + MODIFY_ENTRY + SL viene classificato come UPDATE
4. un vero nuovo signal resta classificato come SIGNAL
5. gli intent update non vengono più scartati come noise in questi casi
```

---

# 10. Proposta 3 — Explicit ID resolution contro external_signal_id

## 10.1 Descrizione

Correggere la risoluzione target degli update con `Signal ID`.

Oggi il comportamento concettualmente errato è:

```text
explicit_id c4
→ confronta con canonical_message_id
```

Il comportamento corretto deve essere:

```text
explicit_id c4
→ confronta con external_signal_id
```

sempre limitando la ricerca al trader sorgente.

---

## 10.2 Regola di match

Input update:

```yaml
trader_id: trader_c
source_chat_id: "-100..."
source_topic_id: 123
target_hints:
  explicit_ids:
    - c4
```

Ricerca:

```text
trader_id = trader_c
source_chat_id = source_chat_id dell’update
source_topic_id = source_topic_id dell’update, se richiesto/configurato
external_signal_id = c4
```

Risultati:

```text
1 match   → EXACT_MATCH
0 match   → REVIEW_REQUIRED: explicit_signal_id_not_found
>1 match  → REVIEW_REQUIRED: explicit_signal_id_ambiguous
```

---

## 10.3 Pseudo-codice

```python
def resolve_by_explicit_id(trader_chains, explicit_ids):
    wanted = {
        normalize_external_signal_id(x)
        for x in explicit_ids
    }

    matched = [
        c for c in trader_chains
        if normalize_external_signal_id(c.external_signal_id) in wanted
    ]

    if len(matched) == 1:
        return matched

    if len(matched) > 1:
        return REVIEW_REQUIRED("explicit_signal_id_ambiguous")

    return REVIEW_REQUIRED("explicit_signal_id_not_found")
```

---

## 10.4 Fallback legacy temporaneo

Durante la transizione, può essere consentito un fallback legacy:

```text
external_signal_id non trovato
→ prova canonical_message_id
```

Ma solo con warning esplicito:

```text
legacy_explicit_id_match_used
```

Raccomandazione: disabilitare questo fallback dopo backfill/migrazione.

---

## 10.5 Esempio corretto

Chain esistente:

```yaml
trade_chain_id: 18
trader_id: trader_c
source_chat_id: "-100..."
external_signal_id: c4
symbol: AAVEUSDT
side: LONG
```

Update:

```text
Signal ID: #c4
Вход по рынку
```

Risultato:

```yaml
target_resolution:
  status: EXACT_MATCH
  matched_by: EXPLICIT_SIGNAL_ID
  trade_chain_id: 18
```

---

## 10.6 Collisione tra trader diversi

Chain:

```yaml
- trade_chain_id: 18
  trader_id: trader_c
  external_signal_id: c4

- trade_chain_id: 25
  trader_id: trader_d
  external_signal_id: c4
```

Update da `trader_c`:

```text
Signal ID: #c4
```

Risultato atteso:

```yaml
matched_chain: 18
```

Non deve matchare `trader_d`.

---

## 10.7 Criteri di accettazione

La proposta 3 è completata quando:

```text
1. explicit_id viene confrontato con external_signal_id
2. il match è sempre limitato al trader dell’update
3. se configurato, viene considerato anche source_chat_id
4. collisioni tra trader diversi non causano match errato
5. zero match produce review
6. più match producono review
7. canonical_message_id non è più la chiave primaria per explicit_id
```

---

# 11. Test richiesti

## 11.1 Test DB / identity

### Test 1 — Signal originale salva external_signal_id

Input:

```text
[trader #c] Signal ID: #c4
#AAVEUSDT LONG
Вход: 61 лимитка
TP: 69.73
SL: 59.57
```

Atteso:

```yaml
trade_chain:
  trader_id: trader_c
  external_signal_id: c4
  symbol: AAVEUSDT
  side: LONG
```

---

### Test 2 — Normalizzazione Signal ID

Input:

```text
Signal ID: #C4
```

Atteso:

```text
external_signal_id = c4
```

---

## 11.2 Test parser classification

### Test 3 — Update mirato non diventa SIGNAL

Input:

```text
Signal ID: #c4
#AAVEUSDT LONG
Вход по рынку
SL 60.73
```

Atteso:

```yaml
primary_class: UPDATE
warnings:
  - signal_like_update_forced_to_update
```

---

### Test 4 — Nuovo segnale resta SIGNAL

Input:

```text
#AAVEUSDT LONG
Вход: 61 лимитка
TP: 69.73
SL: 59.57
Risk: 1%
```

Atteso:

```yaml
primary_class: SIGNAL
parse_status: PARSED
```

---

## 11.3 Test explicit id resolution

### Test 5 — Match via external_signal_id

Setup:

```yaml
chain:
  trade_chain_id: 18
  trader_id: trader_c
  external_signal_id: c4
```

Update:

```yaml
trader_id: trader_c
target_hints:
  explicit_ids:
    - c4
```

Atteso:

```yaml
matched_chain: 18
```

---

### Test 6 — Collisione tra trader

Setup:

```yaml
chain_1:
  trade_chain_id: 18
  trader_id: trader_c
  external_signal_id: c4

chain_2:
  trade_chain_id: 25
  trader_id: trader_d
  external_signal_id: c4
```

Update da `trader_c`.

Atteso:

```yaml
matched_chain: 18
```

---

### Test 7 — Explicit ID non trovato

Update:

```yaml
trader_id: trader_c
target_hints:
  explicit_ids:
    - c999
```

Atteso:

```yaml
review_required: explicit_signal_id_not_found
```

---

### Test 8 — Explicit ID ambiguo

Setup:

```yaml
chain_1:
  trader_id: trader_c
  external_signal_id: c4

chain_2:
  trader_id: trader_c
  external_signal_id: c4
```

Atteso:

```yaml
review_required: explicit_signal_id_ambiguous
```

---

# 12. Criteri di accettazione generali

La patch V1 è accettata quando:

```text
1. ogni nuova trade chain può salvare external_signal_id
2. ogni nuova trade chain può salvare telegram_message_id/source minimi
3. Signal ID viene normalizzato
4. update targettati non vengono classificati come SIGNAL parziali
5. explicit_id viene risolto contro external_signal_id
6. il match explicit_id è trader-scoped
7. zero/multi match non vengono eseguiti automaticamente
8. nessuna modifica rompe il flusso SIGNAL esistente
9. nessuna modifica richiede refactor completo del sistema
```

---

# 13. Decisione tecnica

Questa patch non introduce ancora una nuova architettura `SIGNAL_PLAN_PATCH`.

La strategia è:

```text
usare i componenti esistenti
+ aggiungere identità minima
+ correggere classificazione
+ correggere explicit_id resolution
```

Questa è una patch compatibile, non una riscrittura.

---

# 14. Fuori scope esplicito

Non implementare in questa patch:

```text
- global scope hardening
- target mode completo SINGLE/MULTI/REVIEW
- gestione conflitti single-chain vs global scope
- override SL in MARKET_NOW
- ricalcolo risk con nuovo SL
- PATCH_SIGNAL_PLAN
- clean log multi-chain evoluto
```

Questi punti restano per una patch successiva.

---

# 15. Definition of Done

Dato il segnale:

```text
[trader #c] Signal ID: #c4
#AAVEUSDT LONG
Вход: 61 лимитка
TP: 69.73
SL: 59.57
Risk: 1%
```

il sistema crea una chain con:

```yaml
trader_id: trader_c
external_signal_id: c4
symbol: AAVEUSDT
side: LONG
```

Dato l’update:

```text
[trader #c] Signal ID: #c4
#AAVEUSDT LONG
Вход по рынку
SL 60.73
```

il parser produce:

```yaml
primary_class: UPDATE
target_hints:
  explicit_ids:
    - c4
```

e il runtime risolve:

```yaml
matched_chain:
  trade_chain_id: chain originale
  matched_by: EXPLICIT_SIGNAL_ID
```

senza classificare l’update come nuovo signal parziale e senza confrontare `c4` con `canonical_message_id`.
