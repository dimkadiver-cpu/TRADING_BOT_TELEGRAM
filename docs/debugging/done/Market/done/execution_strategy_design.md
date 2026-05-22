# Execution Strategy Design — Entry con Protezione Attached

## Obiettivo

Definire il comportamento target del runtime per ogni combinazione di entry/TP,
garantendo che il SL sia sempre exchange-native dal momento del placement.

---

## Principio fondamentale

**Il SL deve essere attached all'ordine entry al momento del placement.**

Non e' accettabile che il SL venga creato solo dopo il fill (pattern WAITING_POSITION).
Se il bot cade tra il placement e il fill, la posizione aperta e' scoperta.

Il TP segue una logica diversa: il suo livello di protezione dipende dalla complessita' del setup.

---

## Casi di esecuzione

### Caso_1: 1 entry, 1 TP

**Comportamento target:**

- Entry piazzata con **SL + TP attached** in un unico ordine.
- Se il bot cade dopo il placement: SL e TP esistono su exchange per la qty fillata.
- Nessuna azione richiesta dopo il fill.

**Implementazione attuale:** `C_SIMPLE_ATTACHED` ✅

Condizione routing: `entry_count == 1 AND tp_count == 1 AND sl presente`.

Comando emesso: `PLACE_ENTRY_WITH_ATTACHED_TPSL` con `tpslMode=Full`.

---

### Caso_1_1: 1 entry, 2/3 TP

**Comportamento target:**

- Entry piazzata con **SL attached** (+ opzionalmente ultimo TP attached).
- Dopo il fill: il bot aggiunge i TP intermedi via `SET_POSITION_TPSL_PARTIAL`.
- Se il bot cade dopo il placement ma prima del fill: SL protegge la posizione.
- Se il bot cade dopo il fill ma prima dei TP intermedi: SL protegge, TP intermedi assenti.

**Implementazione attuale:** cade in `D_POSITION_TPSL` ❌

Problema: entry piazzata senza protezione attached; tutti i TP via WAITING_POSITION pre-calcolati.

**Cambiamenti necessari:**

1. `entry_gate.py` — nuova branch routing C estesa: `entry_count == 1 AND tp_count > 1 AND sl presente`
2. Emettere `PLACE_ENTRY_WITH_ATTACHED_TPSL` con SL (e ultimo TP opzionale) attached.
3. Emettere TP intermedi come `WAITING_POSITION` → rilasciati dopo fill.
4. `order_builder.py` — flag per `SET_POSITION_TPSL_PARTIAL` senza sovrascrivere SL quando il SL e' gia' attached order-level.

**Decisione aperta:** attaccare solo SL oppure SL + ultimo TP?

- Solo SL: piu' semplice, TP sempre bot-dependent.
- SL + ultimo TP: protezione target massima, ma i TP intermedi position-level potrebbero sovrapporsi con l'ultimo TP attached se non gestiti correttamente.

---

### Caso_2: 2+ entry LIMIT, 1 TP

**Comportamento target:**

- Ogni entry piazzata con **SL + TP attached** per la propria qty (basata su weight).
- Se il bot cade dopo il fill di Entry1: SL+TP attivi per qty1.
- Quando filla Entry2: SL+TP attivi per qty2 si aggiungono a quelli di Entry1.
- La somma degli SL attached = posizione totale fillata in qualsiasi momento ✅

**Implementazione attuale:** cade in `D_POSITION_TPSL` ❌

Problema: entry piazzate senza protezione; `SET_POSITION_TPSL_FULL` solo dopo il fill.

**Cambiamenti necessari:**

1. `entry_gate.py` `_build_d_commands` — sostituire `PLACE_ENTRY` con `PLACE_ENTRY_WITH_ATTACHED_TPSL` per ogni leg.
2. Qty attached per leg: `risk_amount_leg / abs(leg_price - sl_price)` (o `leg_notional / leg_price`).
3. Rimuovere `SET_POSITION_TPSL_FULL` `WAITING_POSITION` — ridondante, gia' coperto dagli attached.

**Note su Bybit:**

Piu' SL attached order-level coesistono sullo stesso simbolo/direzione senza conflitti se sono `reduce_only`.
Quando il prezzo tocca lo SL, entrambi partono; il secondo trova posizione chiusa o ridotta e si annulla.
Questo e' accettabile e non produce danni.

---

### Caso_2_2: 2+ entry LIMIT, 2/3 TP

**Comportamento target:**

- Ogni entry piazzata con **SL attached** (+ opzionalmente ultimo TP attached) per la propria qty.
- Dopo fill di Entry1: aggiungere TP parziali intermedi calcolati su qty1 fillata.
- Dopo fill di Entry2: **ricalcolare** i TP parziali su qty totale (qty1 + qty2).
  - Cancellare i TP parziali precedenti.
  - Emettere nuovi `SET_POSITION_TPSL_PARTIAL` con qty aggiornate.

**Implementazione attuale:** cade in `D_POSITION_TPSL` ❌

Problema: entry nude; TP pre-calcolati upfront con qty fissa sulla posizione pianificata totale.

**Cambiamenti necessari:**

1. `entry_gate.py` — stessa modifica di Caso_2 per le entry (attached per leg).
2. **`TradeChain` / `risk_snapshot_json`** — salvare prezzi TP e distribuzione (close_pct) nel chain. Attualmente questi dati non sono persistiti: vengono usati per costruire i comandi e poi persi.
3. `event_processor.py` `_process_entry_filled` — dopo ogni fill:
   - Leggere prezzi TP e distribuzione dal `risk_snapshot_json` del chain.
   - Cancellare i comandi TP parziali precedenti (da fill precedente, se esistono).
   - Emettere nuovi `SET_POSITION_TPSL_PARTIAL` calcolati su `filled_entry_qty` totale corrente.
   - Attualmente ritorna `execution_commands=[]` — nessuna azione post-fill.

---

### MARKET (singolo o con LIMIT averaging)

**Stessa logica degli stessi casi.**

I segnali MARKET senza `mark_price` disponibile al momento del parsing vengono processati con
`qty_mode=deferred_market`: il gateway fetcha il mark_price live al momento del submit e calcola
`qty = risk_amount_leg / abs(mark_price - sl_price)`.

Se al submit il mark_price non e' disponibile, il comando passa in `REVIEW_REQUIRED`.

I casi MARKET seguono la stessa matrice degli altri:

- MARKET singolo + 1 TP → Caso_1 con qty calcolata al submit.
- MARKET + LIMIT averaging → Caso_2/Caso_2_2 con qty MARKET deferred, LIMIT deterministica.

---

## Mappa dei cambiamenti al codice

| File | Cambiamento | Caso |
|------|-------------|------|
| `lifecycle/entry_gate.py` | Nuova branch C per 1-entry + multi-TP | Caso_1_1 |
| `lifecycle/entry_gate.py` | `_build_d_commands`: entry con attached per ogni leg | Caso_2, Caso_2_2 |
| `lifecycle/entry_gate.py` | Salvare prezzi TP + distribuzione in risk_snapshot | Caso_2_2 |
| `lifecycle/event_processor.py` | `_process_entry_filled`: emettere TP commands post-fill | Caso_1_1, Caso_2_2 |
| `execution_gateway/adapters/ccxt_bybit/order_builder.py` | Flag per partial TP senza sovrascrivere SL | Caso_1_1 |
| `lifecycle/risk_capacity.py` | ✅ Rimosso blocco MARKET, aggiunto per-leg snapshot | MARKET |
| `execution_gateway/gateway.py` | ✅ Risoluzione qty deferred prima di place_order | MARKET |
| `execution_gateway/adapters/base.py` | ✅ Aggiunto fetch_mark_price all'interfaccia | MARKET |

---

## Matrice routing target

| entry_count | tp_count | sl | Routing | Comando entry | Protezione al placement |
|---|---|---|---|---|---|
| 1 | 1 | si' | C_SIMPLE_ATTACHED | PLACE_ENTRY_WITH_ATTACHED_TPSL (Full) | SL + TP ✅ |
| 1 | >1 | si' | C_MULTI_TP (nuovo) | PLACE_ENTRY_WITH_ATTACHED_TPSL (SL + opz. ultimo TP) | SL ✅ |
| >1 | 1 | si' | D_MULTI_ENTRY_1TP | PLACE_ENTRY_WITH_ATTACHED_TPSL per leg | SL + TP per leg ✅ |
| >1 | >1 | si' | D_MULTI_ENTRY_MULTI_TP | PLACE_ENTRY_WITH_ATTACHED_TPSL per leg | SL per leg ✅ |
| qualsiasi | qualsiasi | no | REVIEW_REQUIRED | n/a | n/a |

---

## Stato implementazione

| Caso | Stato |
|------|-------|
| Caso_1 (1 entry, 1 TP) | ✅ Implementato (C_SIMPLE_ATTACHED) |
| Caso_1_1 (1 entry, N TP) | ❌ Da implementare |
| Caso_2 (N entry, 1 TP) | ❌ Da implementare |
| Caso_2_2 (N entry, N TP) | ❌ Da implementare |
| MARKET deferred | ✅ Implementato (vedi done/market_entry_qty_deferred.md) |
