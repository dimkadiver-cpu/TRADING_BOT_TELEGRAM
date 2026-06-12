# Handoff — sessione 2026-06-12 / fix funding mai registrato

---

## Cosa è stato fatto

Indagine sulla discrepanza segnalata tra report `POSITION CLOSED` (es. #20 XAUT/USDT, `Funding: +-0.00 USDT`) e i dati reali su exchange. Trovati e corretti due bug indipendenti.

### Root cause 1 — funding sempre a zero (strutturale)

ccxt.pro per Bybit ha il default `watchMyTrades.filterExecTypes = ['Trade', 'AdlTrade', 'BustTrade', 'Settle']` (`.venv/.../ccxt/pro/bybit.py:133`): le execution con `execType=Funding` venivano **scartate dentro ccxt** prima di arrivare al normalizer del bot. Tutta la pipeline downstream era già corretta e testata (classifier → `FUNDING_SETTLED` → `_handle_funding_settled` → `cumulative_funding`) ma non riceveva mai eventi.

Evidenza nel DB `db/Test_live/ops.sqlite3`: 0 eventi con `exec_type='Funding'` su 231 raw events; `cumulative_funding = 0.0` su tutte le 17 chain, incluse posizioni aperte da giorni.

Il test preesistente sul funding (`test_ws_funding_event_resolves_raw_symbol_chain_and_forwards_to_lifecycle`) mascherava il bug perché inietta l'evento già normalizzato, bypassando ccxt.

### Root cause 2 — display "+-0.00" (cosmetico)

`outbox_writer._final_result` produce `round(-funding_total, 8)` → con funding 0.0 genera `-0.0` (zero negativo). `money_signed(-0.0)`: `-0.0 >= 0` → prefisso `+`, ma `f"{-0.0:.2f}"` → `-0.00`, da cui `+-0.00 USDT`.

---

## File toccati

```
src/runtime_v2/execution_gateway/adapters/ccxt_bybit/ws_fill_watcher.py
    ← _build_exchange: override filterExecTypes con ['Trade','AdlTrade','BustTrade','Settle','Funding']
src/runtime_v2/control_plane/formatters/_formatters.py
    ← money_signed / pct_signed: normalizzazione -0.0 (number += 0.0)
tests/runtime_v2/execution_gateway/test_bybit_ws_fill_watcher.py
    ← nuovo test: filterExecTypes deve includere Funding
tests/runtime_v2/control_plane/test_blocks_formatters.py
    ← test money_signed(-0.0) e pct_signed(-0.0)
docs/AUDIT.md
    ← nuova entry 2026-06-12
```

---

## Stato attuale

```
pytest tests/runtime_v2/ -q  (working tree)
→ 1174 passed, 19 failed, 6 skipped

pytest tests/runtime_v2/ -q  (HEAD ea4e36b pulito, worktree separato)
→ 1189 passed, 30 failed, 6 skipped
```

I 19 falliti del working tree sono un sottoinsieme esatto dei 30 di HEAD (tutti pre-existing: naming `NOOP_ALREADY_PROTECTED_BE`, `Entry` vs `Entry_1`, eventi `REVIEW_REQUIRED`, live trading gate). Gli 11 in più su HEAD sono in `tests/runtime_v2/test_acceptance.py`, file cancellato tra le modifiche pendenti dell'utente. **Zero regressioni introdotte da questa sessione.**

---

## Rischi aperti

- **Funding storico irrecuperabile via WS**: le chain già chiuse restano con `cumulative_funding=0.0`. Backfill possibile via REST `/v5/execution/list` con `execType=Funding`, non implementato.
- **Riconciliazione REST gira solo su errore WS**: un funding maturato durante downtime del bot viene ripreso solo se la riconciliazione scatta (il percorso REST non filtra per execType, quindi funzionerebbe).
- **Verifica live mancante**: il fix è validato solo a livello di configurazione/unit test. Conferma definitiva: tenere una posizione aperta attraverso un timestamp di funding (00/08/16 UTC) e verificare `cumulative_funding != 0` e l'evento `FUNDING_SETTLED` nel DB.
- **Report #20 XAUT di altro ambiente**: la chain #20 non esiste in `db/Test_live/ops.sqlite3` (max 17); il DB dell'ambiente che ha prodotto quel report ha lo stesso bug ma non è stato ispezionato.
- **19 test pre-existing rossi** sul working tree, non legati a questa sessione.

---

## Prossimo prompt suggerito

> Verifica live del fix funding: avvia il bot in Test_live, apri (o lascia aperta) una posizione attraverso un funding timestamp (00:00/08:00/16:00 UTC) e controlla che in `ops.sqlite3` compaiano raw events con `exec_type='Funding'`, eventi `FUNDING_SETTLED` e `cumulative_funding` aggiornato sulla chain. Valuta poi se implementare il backfill REST del funding per le chain ancora aperte.
