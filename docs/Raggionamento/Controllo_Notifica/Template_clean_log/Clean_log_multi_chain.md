# MULTI_CHAIN — esempi

Template unificato `_MULTI_CHAIN_BLOCKS` con `_t_multi_chain`.
Struttura custom: nessun `HeaderBlock` — prima riga prodotta da `DerivedBlock`.
Nessuna riga symbol/side — il summary copre più chain eterogenee.

## Tipi di evento e quando sono emessi

| Evento | `summary_kind` | Quando emesso |
|--------|---------------|---------------|
| `MULTI_CHAIN_SUMMARY` | `"immediate"` | Update multi-target esplicito (reply/link) — non CLOSE_FULL |
| `MULTI_CHAIN_UPDATE` | `"immediate"` | Update scope globale (`ALL_POSITIONS`, `ALL_OPEN`) — non CLOSE_FULL |
| `MULTI_CHAIN_CLOSED` | `"final_close"` | Update con CLOSE_FULL — emesso ritardato, dopo che i link `POSITION_CLOSED` sono risolvibili |

Tutti e tre usano gli stessi block e lo stesso transform. La differenza è il payload e quando vengono emessi.

## Struttura

```
{emoji} UPDATE APPLICATO — {N} chain
- - -
Operation[s] requested:
▪️ {op}
- - -
#{id} {SYMBOL} {SIDE} — {STATUS}
{link}
[{display_lines}]
- - -
...
Done: N | Partial: N | Skipped: N | Error: N
- - -
Source: trader_update
{update_link}
```

- `emoji` = `✅` se tutti DONE, `⚠️` se almeno uno PARTIAL/SKIPPED/REVIEW/ERROR
- `Operation[s] requested:` — singolare per CLOSE_FULL, plurale altrimenti
- `display_lines` — righe di cambiamento per-chain (omesse in `final_close`)
- `link` per-chain: punta a `SIGNAL_ACCEPTED` root (non CLOSE_FULL) o `POSITION_CLOSED` finale (CLOSE_FULL)

---

## Caso 1 — Non-CLOSE_FULL, 4 chain, 3 DONE + 1 PARTIAL

Update `CANCEL_PENDING + MOVE_SL_TO_BE` su 4 chain. Chain #7 PARTIAL perché Entry_2 non aveva ordine pending.

```
✅ UPDATE APPLICATO — 4 chain
- - - - - - - - - - - - - - - - - - - - - - - -
Operations requested:
▪️ CANCEL_PENDING
▪️ MOVE_SL_TO_BE
- - - - - - - - - - - - - - - - - - - - - - - -
#6 WLD/USDT LONG — DONE
https://t.me/c/3897279123/468
Entry_2: 61,192.03 → cancelled
Entry_3: 60,192.03 → cancelled
SL: 66,400 → 68,500 BE
- - - - - - - - - - - - - - - - - - - - - - - -
#7 ICNT/USDT LONG — PARTIAL
https://t.me/c/3897279123/469
Entry_2: SKIPPED — no pending averaging order
SL: 66,400 → 68,500 BE
- - - - - - - - - - - - - - - - - - - - - - - -
#8 BTC/USDT LONG — DONE
https://t.me/c/3897279123/470
Entry_2: 61,192.03 → cancelled
SL: 66,400 → 68,500 BE
- - - - - - - - - - - - - - - - - - - - - - - -
#1 ICNT/USDT LONG — DONE
https://t.me/c/3897279123/466
Entry_2: 61,192.03 → cancelled
SL: 66,400 → 68,500 BE
- - - - - - - - - - - - - - - - - - - - - - - -
Done: 3 | Partial: 1 | Skipped: 0 | Error: 0
- - - - - - - - - - - - - - - - - - - - - - - -
Source: trader_update
https://t.me/c/3927267771/365
```

> `⚠️` quando almeno una chain è PARTIAL/SKIPPED/REVIEW/ERROR. Qui una PARTIAL → `⚠️`.
> Wait — 3 DONE + 1 PARTIAL = `⚠️`. Corretto.

---

## Caso 2 — CLOSE_FULL, 4 chain, tutte DONE (`final_close`)

Emesso ritardato dopo che tutti i `POSITION_CLOSED` sono risolvibili.
`display_lines` omesse — il link finale rimanda al `POSITION_CLOSED` di ogni chain.

```
✅ UPDATE APPLICATO — 4 chain
- - - - - - - - - - - - - - - - - - - - - - - - - - - - -
Operation requested:
▪️ Close full
- - - - - - - - - - - - - - - - - - - - - - - - - - - - -
#6 WLD/USDT LONG — DONE
https://t.me/c/3897279123/501
- - - - - - - - - - - - - - - - - - - - - - - - - - - - -
#7 ICNT/USDT LONG — DONE
https://t.me/c/3897279123/502
- - - - - - - - - - - - - - - - - - - - - - - - - - - - -
#8 BTC/USDT LONG — DONE
https://t.me/c/3897279123/503
- - - - - - - - - - - - - - - - - - - - - - - - - - - - -
#1 ICNT/USDT LONG — DONE
https://t.me/c/3897279123/504
- - - - - - - - - - - - - - - - - - - - - - - - - - - - -
Done: 4 | Skipped: 0 | Error: 0
- - - - - - - - - - - - - - - - - - - - - - - - - - - - -
Source: trader_update
https://t.me/c/3927267771/365
```

> Link = `POSITION_CLOSED` di ogni chain (non root `SIGNAL_ACCEPTED`).
> Nessuna `display_lines` — il dettaglio chiusura è nel messaggio `POSITION_CLOSED` linkato.
> `Partial:` omesso nei conteggi `final_close` (non rilevante per chiusure).
> Tutti DONE → emoji `✅`.

---

## Caso 3 — Non-CLOSE_FULL, con SKIPPED + ERROR

3 chain: 1 DONE, 1 SKIPPED, 1 ERROR. `⚠️` perché ci sono issues.

```
⚠️ UPDATE APPLICATO — 3 chain
- - - - - - - - - - - - - - - - - - - - - - - -
Operations requested:
▪️ CANCEL_PENDING
- - - - - - - - - - - - - - - - - - - - - - - -
#8 BTC/USDT LONG — DONE
https://t.me/c/3897279123/470
Entry_2: 61,192.03 → cancelled
- - - - - - - - - - - - - - - - - - - - - - - -
#7 ICNT/USDT LONG — SKIPPED
https://t.me/c/3897279123/469
SKIPPED — no pending orders to cancel
- - - - - - - - - - - - - - - - - - - - - - - -
#6 WLD/USDT LONG — ERROR
https://t.me/c/3897279123/468
Error — exchange timeout during cancel
- - - - - - - - - - - - - - - - - - - - - - - -
Done: 1 | Partial: 0 | Skipped: 1 | Error: 1
- - - - - - - - - - - - - - - - - - - - - - - -
Source: trader_update
https://t.me/c/3927267771/380
```

> SKIPPED e ERROR mostrano una riga descrittiva — testo utente leggibile, non raw event names.
> Policy: `NOOP_NOT_PENDING` → `SKIPPED — no pending orders to cancel`; mai esposto il codice interno.

---

## Caso 4 — MOVE_SL_TO_LEVEL con Reference

Update `MOVE_SL_TO_LEVEL` su 2 chain, SL spostato a livello TP_1 per entrambe.

```
✅ UPDATE APPLICATO — 2 chain
- - - - - - - - - - - - - - - - - - - - - - - -
Operations requested:
▪️ MOVE_SL_TO_LEVEL
- - - - - - - - - - - - - - - - - - - - - - - -
#8 BTC/USDT LONG — DONE
https://t.me/c/3897279123/470
SL: 63,000 → 67,950
Reference: TP_1
- - - - - - - - - - - - - - - - - - - - - - - -
#6 WLD/USDT LONG — DONE
https://t.me/c/3897279123/468
SL: 0.280 → 0.310
Reference: TP_1
- - - - - - - - - - - - - - - - - - - - - - - -
Done: 2 | Partial: 0 | Skipped: 0 | Error: 0
- - - - - - - - - - - - - - - - - - - - - - - -
Source: trader_update
https://t.me/c/3927267771/381
```

> `Reference: TP_1` — valori ammessi: `TP_1`, `TP_2`, `TP_3`, `Price`. Sempre riga separata dopo il delta SL.
> Per MOVE_SL_TO_BE (breakeven) la riga Reference non compare — la semantica BE è già in `→ 65,000 BE`.

---

## Caso 5 — Scope globale ALL_POSITIONS (`MULTI_CHAIN_UPDATE`)

Update con scope globale su 5 chain. Usa `MULTI_CHAIN_UPDATE` (non `MULTI_CHAIN_SUMMARY`).

```
⚠️ UPDATE APPLICATO — 5 chain
- - - - - - - - - - - - - - - - - - - - - - - -
Operations requested:
▪️ MOVE_SL_TO_BE
- - - - - - - - - - - - - - - - - - - - - - - -
#6 WLD/USDT LONG — DONE
https://t.me/c/3897279123/468
SL: 66,400 → 68,500 BE
- - - - - - - - - - - - - - - - - - - - - - - -
#7 ICNT/USDT LONG — DONE
https://t.me/c/3897279123/469
SL: 0.280 → 0.310 BE
- - - - - - - - - - - - - - - - - - - - - - - -
#8 BTC/USDT LONG — SKIPPED
https://t.me/c/3897279123/470
SKIPPED — already at breakeven
- - - - - - - - - - - - - - - - - - - - - - - -
#1 ICNT/USDT LONG — DONE
https://t.me/c/3897279123/466
SL: 0.280 → 0.310 BE
- - - - - - - - - - - - - - - - - - - - - - - -
#3 ETH/USDT SHORT — DONE
https://t.me/c/3897279123/471
SL: 3,100 → 3,200 BE
- - - - - - - - - - - - - - - - - - - - - - - -
Done: 4 | Partial: 0 | Skipped: 1 | Error: 0
- - - - - - - - - - - - - - - - - - - - - - - -
Source: trader_update
https://t.me/c/3927267771/382
```

> Chain #8 SKIPPED — già a BE, operazione non applicata. Motivo leggibile in `display_lines`.
> `⚠️` per via dello SKIPPED.

---

## Note implementative

### `display_lines` — formato pre-costruito

Le righe per-chain (`display_lines`) sono stringhe già formattate dal lifecycle layer:

| Operazione | Formato riga |
|------------|-------------|
| CANCEL_PENDING (DONE) | `Entry_N: {price} → cancelled` |
| CANCEL_PENDING (SKIPPED) | `Entry_N: SKIPPED — {motivo}` |
| MOVE_SL_TO_BE | `SL: {old} → {new} BE` |
| MOVE_SL_TO_LEVEL (non BE) | `SL: {old} → {new}` + riga `Reference: {ref}` |
| SKIPPED catena intera | `SKIPPED — {motivo leggibile}` |
| ERROR catena intera | `Error — {motivo breve}` |

Policy: nessun raw event name (`NOOP_*`, `REVIEW_REQUIRED`) nel testo finale — sempre testo utente leggibile.

### `summary_kind` e comportamento `_render_chain_item`

| `summary_kind` | `display_lines` mostrate | `_fmt_counts` include `Partial:` |
|---------------|--------------------------|----------------------------------|
| `"immediate"` | sì | sì |
| `"final_close"` | no | no |

### Link per-chain

| Tipo update | Link chain punta a |
|-------------|-------------------|
| Non-CLOSE_FULL | `SIGNAL_ACCEPTED` root della chain |
| CLOSE_FULL | `POSITION_CLOSED` finale della chain |
