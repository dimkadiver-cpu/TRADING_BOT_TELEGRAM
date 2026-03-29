# Fase 6 - Allineamento operation_rules vs execution

Questo documento serve come handoff operativo per un agente che deve chiudere le discrepanze tra `operation_rules` e l'esecuzione reale della strategy.

Riferimenti:

- `docs/PRD_FASE_6.md`
- `docs/FASE_6_COMPLETAMENTO.md`
- `docs/FREQTRADE_CONFIG.md`
- `freqtrade/user_data/strategies/SignalBridgeStrategy.py`
- `src/operation_rules/engine.py`
- `src/telegram/router.py`
- `src/execution/freqtrade_normalizer.py`
- `src/execution/exchange_order_manager.py`

## Obiettivo

Allineare il comportamento runtime di `SignalBridgeStrategy` e del lato execution con le regole gia calcolate e persistite da `operation_rules`, evitando divergenze tra piano teorico e comportamento reale.

## Stato attuale

Le parti oggi allineate sono:

- `position_size_usdt` -> usata da `custom_stake_amount()`
- `leverage` -> usata da `leverage()`
- `tp_handling` -> usata sia dalla strategy sia da `exchange_order_manager`

Le parti oggi non allineate sono elencate sotto.

## Discrepanze confermate

### 1. `entry_split` non diventa multi-entry reale

`operation_rules` calcola e salva `entry_split_json`, ma la strategy emette un solo trigger di ingresso e non materializza `E1`, `E2`, `E3` come ordini distinti.

Impatto:

- `ZONE -> endpoints -> E1/E2` oggi non produce due ordini limit
- il piano di entry calcolato resta auditabile, ma non operativo

Evidenza:

- `src/operation_rules/engine.py` calcola `entry_split`
- `src/telegram/router.py` salva `entry_split_json`
- `freqtrade/user_data/strategies/SignalBridgeStrategy.py` usa solo `enter_long` / `enter_short`

### 2. I prezzi di entry del segnale non sono vincolanti a runtime

Il piano entry del segnale viene salvato in `signals.entry_json`, ma la strategy non implementa `custom_entry_price()` e non blocca l'ingresso se il prezzo runtime esce dalla entry zone.

Impatto:

- il modello risk-first puo usare una distanza SL calcolata su prezzi del segnale
- il fill reale puo avvenire fuori range e modificare il rischio effettivo

Evidenza reale osservata:

- `BTCUSDT`: segnale `66100-66200`, fill `66222.6`
- `XLMUSDT`: segnale `0.1625-0.1635`, fill `0.1658`

### 3. Il tipo di piano entry viene degradato nel router

Il router costruisce `signals.entry_json` come lista di prezzi con `type = LIMIT`, anche quando il parser/engine distinguono piani `MARKET`, `MARKET_WITH_LIMIT_AVERAGING` o `LIMIT_WITH_LIMIT_AVERAGING`.

Impatto:

- il contratto tra parser, operation rules ed execution perde informazione
- il runtime non puo sapere con fedelta il piano di ingresso originario

### 4. `position_management.trader_hint` non governa davvero il runtime

`auto_apply_intents` e `log_only_intents` vengono snapshotati nel DB, ma il runtime supporta intent selezionati tramite logica hardcoded.

Impatto:

- cambiare il file YAML non cambia davvero il comportamento automatico del runtime
- il DB mostra una configurazione che non e la vera source of truth operativa

### 5. `position_management.machine_event.rules` non e eseguito

Le regole machine-event vengono persistite dentro `management_rules_json`, ma oggi non risultano consumate da strategy o manager.

Impatto:

- regole come `TP_EXECUTED -> MOVE_STOP_TO_BE` non vengono applicate dal motore configurabile
- resta solo comportamento codificato altrove o assente

### 6. `price_corrections` non e cablato

La sezione esiste nel modello regole, ma il router salva `price_corrections_json = null` e non c'e consumo lato execution.

Impatto:

- la feature e dichiarata ma non operativa
- aumenta la distanza tra documentazione/config e runtime reale

### 7. `price_sanity` protegge il segnale, non il prezzo realmente eseguito

Il gate statico viene applicato sui prezzi parseati dal messaggio, non sul prezzo finale usato dalla strategy/freqtrade.

Impatto:

- passa il gate iniziale
- poi il trade puo essere eseguito fuori dal perimetro che si voleva proteggere

## Decisioni da prendere prima del coding

L'agente deve bloccare queste decisioni prima di modificare il runtime:

1. `entry_split` deve diventare davvero multi-entry live oppure solo una policy per scegliere un unico prezzo di ingresso.
2. Il prezzo del segnale deve essere vincolante con `custom_entry_price()` oppure con hard reject in `confirm_trade_entry()`.
3. `position_management` deve diventare config-driven reale oppure va ridotto esplicitamente al sottoinsieme oggi supportato.
4. `machine_event.rules` va implementato adesso oppure dichiarato come non supportato in modo esplicito e testato.
5. `price_corrections` va implementato oppure rimosso dal contratto esecutivo corrente.

## Ordine consigliato di lavoro

1. Preservare il contratto entry dal parser fino al runtime.
2. Decidere e implementare il modello di esecuzione entry.
3. Rendere coerente il controllo prezzo segnale vs prezzo runtime.
4. Rendere `position_management` realmente source-of-truth oppure ridurre il contratto supportato.
5. Aggiornare documentazione, test e audit.

## Vincoli architetturali

- Nessun doppio owner tra strategy e manager.
- Nessuna logica exchange sparsa fuori dal gateway/manager.
- Il DB resta control-plane e audit trail.
- Ogni comportamento supportato deve essere testato end-to-end.
- Ogni comportamento non supportato deve essere dichiarato esplicitamente nei docs e nei test.

## Criteri di accettazione

Il lavoro e chiuso solo se esiste evidenza concreta che:

- il piano entry usato dal runtime e coerente con quello calcolato da `operation_rules`
- il prezzo di ingresso reale non puo divergere silenziosamente dalla entry policy scelta
- `position_management` non mente: o governa davvero il runtime, o e ridotto al sottoinsieme supportato
- non esistono regole config dichiarate ma ignorate senza evidenza
- i test coprono sia backward compatibility sia il nuovo contratto allineato

## Query utili di verifica

Confronto tra entry teorica e fill reale:

```sql
SELECT s.attempt_key, s.symbol, s.entry_json, o.price AS filled_entry_price, o.qty
FROM signals s
JOIN orders o
  ON o.attempt_key = s.attempt_key
 AND o.purpose = 'ENTRY'
 AND o.status = 'FILLED'
ORDER BY o.updated_at DESC, o.order_pk DESC;
```

Controllo snapshot regole operative:

```sql
SELECT attempt_key, entry_split_json, management_rules_json
FROM operational_signals
WHERE message_type = 'NEW_SIGNAL'
ORDER BY op_signal_id DESC;
```

## Nota operativa

La config runtime osservata al momento della verifica usa ancora:

```json
{
  "execution": {
    "protective_orders_mode": "strategy_managed"
  }
}
```

Quindi il lavoro di allineamento entry/management va valutato tenendo conto sia del percorso legacy `strategy_managed` sia del percorso `exchange_manager`.
