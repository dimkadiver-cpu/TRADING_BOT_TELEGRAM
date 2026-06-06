# Notifiche semplici — esempi

Copertura: `PENDING_ENTRY_EXPIRED`, `REENTRY_ACCEPTED`, `CANCEL_FAILED`,
`RECONCILIATION_WARNING`, `RECONCILIATION_FIXED`.

---

## PENDING ENTRY EXPIRED

Ordine limit pending scaduto prima del fill. Trigger: `timeout_worker`.

```
⏰ #12 — PENDING ENTRY EXPIRED
- - - - - - - - - - - - - - -
BTC/USDT — 📈 LONG
https://t.me/c/3897279123/480
- - - - - - - - - - - - - - -
Timeout: order expired before fill
- - - - - - - - - - - - - - -
Source: timeout_worker
```

> `signal_link` mostrato da `HeaderBlock` — letto dalla chain (link al segnale originale accettato).
> Non indica quale entry specifica è scaduta — informazione disponibile nei log runtime.
> `_PENDING_TIMEOUT_BLOCKS`: `HeaderBlock` + `StaticBlock` + `FooterBlock`.

---

## REENTRY ACCEPTED

Nuova chain aperta per lo stesso segnale (re-entry dopo chiusura precedente).

**Con previous chain:**

```
🔄 #13 — REENTRY ACCEPTED
- - - - - - - - - - - - - - -
BTC/USDT — 📈 LONG
https://t.me/c/3897279123/480
- - - - - - - - - - - - - - -
Previous chain: #12
- - - - - - - - - - - - - - -
Source: runtime
```

**Senza previous chain** (`previous_chain_id` assente → `FieldBlock` opzionale omessa):

```
🔄 #13 — REENTRY ACCEPTED
- - - - - - - - - - - - - - -
BTC/USDT — 📈 LONG
https://t.me/c/3897279123/480
- - - - - - - - - - - - - - -
Source: runtime
```

---

## CANCEL FAILED

L'entry pending non è stata cancellata dopo N tentativi — richiede intervento manuale.
Trigger: `timeout_worker` dopo retry esauriti.

```
🚨 #12 — CANCEL FAILED
- - - - - - - - - - - - - - -
BTC/USDT — 📈 LONG
https://t.me/c/3897279123/480
- - - - - - - - - - - - - - -
Cancellation of Entry_2 failed after 3 attempts.
Requires manual review to resolve the position.
Entry price: 64,000
- - - - - - - - - - - - - - -
Source: timeout_worker
```

> `entry_ref` dal payload → `p.get('entry_ref', 'entry')` (default: `"entry"` se assente).
> Numero tentativi da `p.get('attempts', 3)`.
> `Entry price:` mostra il prezzo dell'ordine che non si riesce a cancellare.

---

## RECONCILIATION WARNING

Sistema ha rilevato discrepanza tra stato locale e stato exchange.

**Chain-specific** (qty mismatch su posizione aperta):

```
⚠️ #12 — RECONCILIATION WARNING
- - - - - - - - - - - - - - -
BTC/USDT — 📈 LONG
https://t.me/c/3897279123/480
- - - - - - - - - - - - - - -
Issue: position_qty_mismatch
Risk: qty delta 0.002 BTC (local: 0.010, exchange: 0.008)
Action: reconcile_position
- - - - - - - - - - - - - - -
Source: runtime
```

**Sistema globale** (ordine orfano non legato a una chain):

```
⚠️ — RECONCILIATION WARNING
- - - - - - - - - - - - - - -
Issue: orphaned_order_detected
Risk: unreferenced open order on exchange
Action: manual_review
- - - - - - - - - - - - - - -
Source: runtime
```

> Senza `chain_id` e `symbol` nel payload: header senza `#N` e senza symbol/side.
> `Issue`, `Risk`, `Action` sono stringhe libere costruite dal worker di riconciliazione.

---

## RECONCILIATION FIXED

Discrepanza risolta automaticamente dal sistema.

**Chain-specific:**

```
✅ #12 — RECONCILIATION FIXED
- - - - - - - - - - - - - - -
BTC/USDT — 📈 LONG
https://t.me/c/3897279123/480
- - - - - - - - - - - - - - -
Issue resolved: position_qty_mismatch
- - - - - - - - - - - - - - -
Source: runtime
```

**Sistema globale:**

```
✅ — RECONCILIATION FIXED
- - - - - - - - - - - - - - -
Issue resolved: orphaned_order_removed
- - - - - - - - - - - - - - -
Source: runtime
```

---

## Note implementative

| Tipo | Trigger | `chain_id` | `symbol/side` | `signal_link` |
|------|---------|-----------|--------------|--------------|
| `PENDING_ENTRY_EXPIRED` | timeout_worker | sì | sì | sì (dal chain) |
| `REENTRY_ACCEPTED` | runtime | sì (nuova chain) | sì | sì (dal chain) |
| `CANCEL_FAILED` | timeout_worker | sì | sì | sì (dal chain) |
| `RECONCILIATION_WARNING` | runtime/worker | opzionale | opzionale | opzionale |
| `RECONCILIATION_FIXED` | runtime/worker | opzionale | opzionale | opzionale |

`signal_link` iniettato da `HeaderBlock` — letto da `payload["signal_link"]`, che viene dalla chain (link al segnale originale accettato). Presente su tutti gli eventi chain-specific.
`RECONCILIATION_*` senza chain_id: `HeaderBlock` omette `#N`. Senza symbol: linea symbol/side assente.
`CANCEL_FAILED`: notifica critica — indica che un ordine exchange non controllato è aperto.
`REENTRY_ACCEPTED`: il `chain_id` nel header è quello della **nuova** chain, non della precedente.
