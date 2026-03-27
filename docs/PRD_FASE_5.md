# PRD — Fase 5: Sistema 1 (Esecuzione Live via freqtrade)

> **Stato:** BOZZA — da approvare prima di Step 16
> **Prerequisiti:** Fase 4 completa (Steps 12–15 ✓, 427/427 test, 2026-03-27)
> **Riferimento:** PRD_generale.md — Sistema 1

---

## Obiettivo

Fase 5 implementa Sistema 1: esecuzione live dei segnali su exchange tramite **freqtrade**.

Il bridge legge i `ResolvedSignal` (status=PENDING in `signals`) prodotti da Fase 4 e li traduce
in segnali freqtrade tramite una IStrategy custom. freqtrade gestisce l'esecuzione ordini su
Bybit via ccxt, il lifecycle posizioni, e l'interfaccia FreqUI + bot Telegram.

**Il bot non esegue ordini direttamente.** Tutto passa per freqtrade.

---

## Architettura

```
signals (status=PENDING)          ← prodotti da Fase 4
operational_signals
      ↓
Signal Bridge IStrategy           freqtrade/user_data/strategies/SignalBridgeStrategy.py  [NUOVO]
  → freqtrade chiama confirm_trade_entry() / custom_stoploss() / custom_exit()
  → IStrategy legge dal nostro DB SQLite
  → restituisce segnali a freqtrade
      ↓
freqtrade                         processo separato
  → esegue ordini su Bybit via ccxt
  → gestisce lifecycle posizioni
  → espone FreqUI (porta 8080)
  → Telegram bot comandi (/status, /forcesell, etc.)
      ↓
DB Callback Writer                src/execution/freqtrade_callback.py  [NUOVO]
  → freqtrade chiama callback su fill, SL hit, TP hit, close
  → aggiorna il nostro DB: signals, trades, orders, positions
  → usa update_applier esistente per le transizioni di stato
```

---

## Signal Bridge — IStrategy

La IStrategy è il cuore del bridge. Implementa i metodi freqtrade necessari:

### Metodi richiesti

```python
class SignalBridgeStrategy(IStrategy):

    def populate_indicators(self, df, metadata):
        # nessun indicatore tecnico — segnali esterni
        return df

    def populate_entry_trend(self, df, metadata):
        # legge signals (status=PENDING, symbol=metadata['pair'])
        # se trovato → setta enter_long / enter_short
        return df

    def populate_exit_trend(self, df, metadata):
        # legge UPDATE signals risolti sulla posizione attiva
        # U_CLOSE_FULL → setta exit_long / exit_short
        return df

    def custom_stoploss(self, pair, trade, current_time, current_rate, current_profit, **kwargs):
        # legge SL dal nostro DB per il trade attivo
        # supporta U_MOVE_STOP (breakeven o nuovo livello)
        return self.stoploss  # default se nessun override

    def confirm_trade_entry(self, pair, order_type, amount, rate, ...):
        # gate finale: ricontrolla eligibility dal DB
        # può rifiutare se signal nel frattempo CANCELLED/INVALID
        return True / False

    def custom_exit(self, pair, trade, current_time, current_rate, current_profit, **kwargs):
        # U_CLOSE_PARTIAL → forza uscita parziale
        return None / "signal_close_partial"
```

### Lettura DB

La IStrategy legge dal DB del bot (SQLite) in **read-only** durante il ciclo di freqtrade.
Non scrive mai direttamente nel DB — delega a `freqtrade_callback.py`.

Connessione: percorso DB configurato in `freqtrade/user_data/config.json` come variabile
d'ambiente o parametro strategia.

---

## DB Callback Writer

`src/execution/freqtrade_callback.py` riceve gli hook freqtrade e aggiorna il nostro DB:

| Hook freqtrade | Azione sul DB |
|---|---|
| `order_filled_callback` | UPDATE signals status=ACTIVE, INSERT into trades, events |
| `trade_exit_callback` | UPDATE trades state=CLOSED, close_reason, UPDATE positions size=0 |
| `stoploss_callback` | UPDATE orders purpose=SL status=FILLED, UPDATE trades CLOSED |
| `partial_exit_callback` | UPDATE trades meta_json (close_fraction) |

Usa `update_applier.apply_update_plan()` già implementato per le transizioni DB.

---

## UPDATE signals → freqtrade

Gli UPDATE intents (U_MOVE_STOP, U_CLOSE_FULL, U_CLOSE_PARTIAL, U_CANCEL_PENDING) sono gestiti
da due layer:

1. **DB layer** — `update_planner` + `update_applier` aggiornano il DB immediatamente (già implementato)
2. **freqtrade layer** — la IStrategy legge lo stato aggiornato al prossimo ciclo e agisce di conseguenza:
   - U_MOVE_STOP → `custom_stoploss()` restituisce il nuovo livello
   - U_CLOSE_FULL → `populate_exit_trend()` setta exit signal
   - U_CLOSE_PARTIAL → `custom_exit()` triggera uscita parziale
   - U_CANCEL_PENDING → `confirm_trade_entry()` restituisce False

---

## Struttura file — Fase 5

```
freqtrade/
└── user_data/
    ├── config.json                  ← config freqtrade (exchange, pairs, stake)
    └── strategies/
        └── SignalBridgeStrategy.py  ← IStrategy custom  [NUOVO]

src/
└── execution/
    ├── freqtrade_callback.py        ← callback writer → DB  [NUOVO]
    ├── update_planner.py            ← già implementato ✓
    └── update_applier.py            ← già implementato ✓
```

### File da eliminare (non più nel design)

I seguenti stub sono stati creati anticipatamente ma sono incompatibili con l'architettura
freqtrade. Vanno rimossi prima di Step 16 per evitare confusione:

```
src/exchange/adapter.py      ← DA ELIMINARE
src/exchange/bybit_rest.py   ← DA ELIMINARE
src/exchange/bybit_ws.py     ← DA ELIMINARE
src/exchange/reconcile.py    ← DA ELIMINARE
src/execution/planner.py     ← DA ELIMINARE (sostituito dalla IStrategy)
src/execution/state_machine.py ← DA ELIMINARE (gestito da freqtrade)
```

**Prima di eliminare:** verificare che nessun test li importi.

---

## DB — tabelle coinvolte

| Tabella | Chi legge | Chi scrive | Note |
|---|---|---|---|
| `signals` | IStrategy (PENDING) | freqtrade_callback | bridge centrale |
| `operational_signals` | IStrategy (size, leverage, rules) | — | read-only per IStrategy |
| `trades` | IStrategy (stato attivo) | freqtrade_callback | lifecycle trade |
| `orders` | — | freqtrade_callback | fill, SL, TP orders |
| `positions` | IStrategy | freqtrade_callback | size corrente |
| `events` | — | freqtrade_callback | audit |

Nessuna nuova migration prevista per Step 16.

---

## Configurazione freqtrade

File `freqtrade/user_data/config.json` (non nel repo — generato da template):
- exchange: bybit
- pair_whitelist: dinamico (letto da `signals` PENDING)
- stake_currency: USDT
- dry_run: true per i primi test
- telegram: configurazione bot controllo

---

## Ordine di sviluppo

```
Step 16  Eliminare src/exchange/ e stub execution inutili
         Creare freqtrade/user_data/strategies/SignalBridgeStrategy.py (scheletro)
         Implementare populate_entry_trend() + confirm_trade_entry()
         Test: dry_run freqtrade con segnale fixture nel DB

Step 17  Implementare custom_stoploss() + populate_exit_trend()
         Implementare src/execution/freqtrade_callback.py
         Test: ciclo completo NEW_SIGNAL → fill → close in dry_run

Step 18  Implementare custom_exit() per U_CLOSE_PARTIAL
         Test UPDATE intents: U_MOVE_STOP, U_CLOSE_FULL su trade dry_run

Step 19  Configurazione freqtrade/user_data/config.json per Bybit live
         Smoke test: 1 segnale reale in paper trading

Step 20  config/channels.yaml con canali reali
         Monitoraggio FreqUI + bot Telegram comandi base
```

---

## Dipendenze nuove

```
freqtrade>=2024.0    # processo separato, installare nel proprio venv
```

La IStrategy viene eseguita nel processo freqtrade, non nel venv del bot.
Il DB SQLite è condiviso tra il processo bot e freqtrade (accesso read/write coordinato).

---

## Rischi aperti

1. **Accesso concorrente al DB** — bot scrive in `signals`, freqtrade legge/scrive via callback.
   Mitigazione: freqtrade_callback usa transazioni SQLite con retry su SQLITE_BUSY.

2. **Pair whitelist dinamica** — freqtrade deve sapere quali pair monitorare prima di ricevere il segnale.
   Mitigazione: config `pair_whitelist` ampia + filtro in `confirm_trade_entry()`.

3. **Timing ciclo freqtrade** — freqtrade chiama `populate_entry_trend()` ogni candle (1m/5m).
   Latenza massima accettabile: 1 ciclo (60s a 1m timeframe).

4. **dry_run → live transition** — tutti i test in dry_run prima di attivare live.
   Mitigazione: `dry_run: true` in config, cambio esplicito richiede PR.

5. **UPDATE intents su trade in attesa di fill** — race condition se UPDATE arriva prima del fill.
   Mitigazione: `confirm_trade_entry()` ricontrolla DB status al momento dell'esecuzione.

---

*Creato: 2026-03-27 (inizio Fase 5) — da approvare prima di Step 16.*
