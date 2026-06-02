# Sincronizzazione eventi exchange — panoramica

Questo documento descrive come il sistema riceve gli eventi dall'exchange, cosa salva nel
database e quali azioni automatiche esegue. Versione semplice: nessun riferimento al codice,
nessuna firma di funzione.

---

## Cosa fa il sistema

Il sistema apre ordini su Bybit seguendo i segnali Telegram, poi ascolta in tempo reale
cosa succede sull'exchange: fill di entry, take profit, stop loss, chiusure manuali.
Ogni evento viene normalizzato, attribuito alla trade chain giusta e persistito nel database.
I worker del lifecycle leggono questi eventi e aggiornano lo stato della chain, producono
automatismi (breakeven, cancel, rebuild TP) e tengono i dati aggiornati.

---

## Come arrivano i dati dall'exchange

Il sistema usa tre livelli in cascata, dal più veloce al più sicuro.

### Livello 1 — WebSocket (primario)

Connessione persistente a tre stream Bybit:

- **watch_orders**: aggiornamenti sullo stato degli ordini (filled, cancelled)
- **watch_my_trades**: esecuzioni singole (execId, prezzo, quantità, fee)
- **watch_positions**: aggiornamenti sulle posizioni aperte (size, TP/SL attached)

Ogni evento WS viene normalizzato, classificato e salvato nel DB in pochi millisecondi.
Se il WS si disconnette, il sistema lancia automaticamente il livello 2.

### Livello 2 — REST polling su comandi inviati (fallback)

Ogni comando inviato all'exchange (SENT/ACK) viene interrogato via REST finché non
risulta filled o cancelled. È il safety net per i comandi la cui conferma WS è stata persa.

### Livello 3 — REST riconciliazione posizioni e trade

Tre controlli aggiuntivi eseguiti periodicamente:

- **Position reconciliation**: rileva posizioni chiuse esternamente (close manuale sull'app
  Bybit, liquidazione) confrontando la size reale della posizione con quella attesa nel DB.
- **Trade-based reconciliation**: recupera le esecuzioni recenti via REST per trovare fill
  di TP che il WS potrebbe aver perso durante downtime.
- **Protective orders reconciliation**: rileva quando un TP attached a livello di posizione
  è stato rimosso esternamente senza essere stato fillato.

---

## Cosa viene salvato nel DB

### Per ogni trade chain (posizione)

| Dato | Dove | Note |
|------|------|-------|
| Prezzo medio di entrata | `ops_trade_chains.entry_avg_price` | media pesata aggiornata a ogni fill di entry |
| Quantità entrata totale | `ops_trade_chains.filled_entry_qty` | somma di tutti i fill entry |
| Quantità posizione aperta | `ops_trade_chains.open_position_qty` | diminuisce a ogni TP/close |
| Quantità posizione chiusa | `ops_trade_chains.closed_position_qty` | aumenta a ogni TP/close |
| PnL lordo cumulato | `ops_trade_chains.cumulative_gross_pnl` | `qty × (fill_price − entry_avg) × sign(side)` |
| Fee cumulate | `ops_trade_chains.cumulative_fees` | somma delle `exec_fee` di ogni fill |
| Funding fee cumulate | `ops_trade_chains.cumulative_funding` | campo presente, **non ancora scritto** |
| Stop loss corrente | `ops_trade_chains.current_stop_price` | aggiornato a ogni move stop confermato |
| Margine allocato | `ops_trade_chains.allocated_margin` | da `risk_snapshot` al momento della creazione |

### Per ogni evento grezzo ricevuto dall'exchange

Tutto il raw è conservato in `exchange_raw_events`: prezzo di esecuzione, quantità, fee,
fee rate, valore in USDT, quantità posizione residua, TP/SL della posizione, timestamp
exchange e il JSON completo dell'info originale. Questa tabella non viene mai modificata
dopo l'inserimento — è audit puro.

### Per ogni decisione del lifecycle

`ops_lifecycle_events` registra ogni decisione: entry aperta, TP colpito, SL colpito,
stop spostato, averaging cancellata, chain chiusa. Ogni evento contiene `fill_price`,
`filled_qty` e `exec_fee` nel suo payload JSON.

---

## Automatismi attivi

### All'apertura di un segnale

- Creazione chain in stato `WAITING_ENTRY`
- Pianificazione ordini di entry (con o senza SL attached a seconda della modalità)
- Pianificazione ordini TP/SL in stato "in attesa del fill" (`WAITING_POSITION`)
- Timeout automatico configurabile: se il fill non arriva entro N ore, la chain scade

### Al primo fill di entry

- Chain passa a `OPEN`
- Prezzo medio e quantità aggiornati
- Gli ordini TP/SL in attesa vengono sbloccati e inviati all'exchange
- Se il piano prevede TP intermedi, vengono ricostruiti (`REBUILD_PARTIAL_TPS`)

### Al fill di un TP

- Quantità posizione ridotta
- Se è il TP finale → chain `CLOSED`
- Se è un TP intermedio → chain `PARTIALLY_CLOSED`
- Se configurato: cancella automaticamente le averaging leg ancora pendenti
- Se configurato: sposta lo stop a breakeven (`MOVE_STOP_TO_BREAKEVEN`)
- Race condition gestita: se serve sia cancellare le averaging sia spostare il BE,
  il BE viene differito finché tutte le cancellazioni sono confermate

### Al fill dello stop loss

- Chain `CLOSED`, quantità azzerata

### Alla chiusura manuale (esterna o via Telegram)

- Chain `CLOSED`, ordini pendenti puliti automaticamente

### Agli update Telegram

- Move stop → `MOVE_STOP_TO_BREAKEVEN` o `MOVE_STOP`
- Close full/partial → ordini di chiusura immediati
- Cancel pending → annulla le entry non ancora fillate
- Modify entries → diff del piano, cancel e re-entry secondo le differenze

### Al timeout dei pending

- Chain `EXPIRED`
- Cancel automatico di tutte le entry in attesa

---

## Gap attuali

### 1. PnL lordo e fee sono nel DB ma non raggiungono l'interfaccia

`cumulative_gross_pnl` e `cumulative_fees` vengono accumulati correttamente nel DB
dopo ogni fill, ma il modello `TradeChain` non espone questi campi e la vista `/pnl`
del control plane mostra letteralmente `"Realized PnL: n/a"`. I dati ci sono, non vengono
mostrati.

### 2. Funding fee non tracciate

Il campo `cumulative_funding` esiste nello schema del DB ma nessun worker lo scrive.
Le funding fee pagate/ricevute durante la vita della posizione non vengono registrate.
Il PnL netto reale (lordo − fee − funding) non è quindi calcolabile.

### 3. Chiusura TP attached rimossa esternamente non gestita nel lifecycle

Quando Bybit rileva che un TP attached a livello di posizione è stato rimosso senza fill,
il sistema inserisce un evento `PROTECTIVE_ORDER_CANCELLED` nel DB. Tuttavia il
`LifecycleEventProcessor` non ha un handler per questo tipo di evento, quindi la chain
resta nello stato corrente senza nessuna reazione automatica.

---

*Fonte: analisi del codice in `src/runtime_v2/` — giugno 2026.*
*Per i riferimenti tecnici precisi vedere `exchange_sync_technical.md`.*
