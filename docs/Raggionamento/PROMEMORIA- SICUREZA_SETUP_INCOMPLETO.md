## Promemoria — caso ASRUSDT `PARTIAL` accettato

Data: 2026-06-07

### Caso osservato

- Messaggio Telegram `389`
- Symbol: `ASRUSDT`
- Trader: `trader_prova`
- Chain: `#14`
- Notifica ricevuta: `SIGNAL ACCEPTED`
- Nota in notifica: `Parser: PARTIAL (incomplete parse)`
- Fallimento exchange: `Bybit retCode 10001: slOrderType can not have a value when tpSlMode is empty`

### Evidenze DB

- `parser.sqlite3`
  - `canonical_message_id = 25`
  - `parse_status = "PARTIAL"`
  - `primary_class = "SIGNAL"`
  - `signal.completeness = "INCOMPLETE"`
  - `missing_fields = ["take_profits"]`
- `ops.sqlite3`
  - `trade_chain_id = 14`
  - evento `SIGNAL_ACCEPTED` con payload `{"parse_status": "PARTIAL"}`
  - command `PLACE_ENTRY_WITH_ATTACHED_TPSL`
  - payload `attached_tpsl.mode = "SL_ONLY"`
  - command fallito con errore Bybit sopra

### Bug identificati

1. Lifecycle gating bug
   - Un `SIGNAL` con `parse_status = PARTIAL` e `missing_fields` critici viene comunque accettato.
   - Atteso: reject o `REVIEW_REQUIRED` quando mancano campi esecutivi essenziali come `take_profits`.

2. Bybit builder bug
   - Nel path `SL_ONLY`, `_place_entry_with_attached_tpsl()` invia `slOrderType` anche quando `tpslMode` non e impostato.
   - Effetto: Bybit rifiuta il payload.

3. Parser diagnostics / UX bug
   - Il parser rileva correttamente `missing_fields = ["take_profits"]`, ma `warnings_json` resta vuoto.
   - La notifica mostra solo il fallback generico `incomplete parse` invece di una causa esplicita.

4. Probabile parser classification bug nel profilo `trader_prova`
   - Sul messaggio completo successivo (`canonical_message_id = 28`, chain `#15`) compare `primary_intent = "TP_HIT"` pur essendo un nuovo segnale.
   - Sospetto: il marker `TP1` viene interpretato anche come report event `TP_HIT`, non solo come take profit label.

### Ordine consigliato dei fix

1. Bloccare nel lifecycle i `SIGNAL PARTIAL` con campi mancanti critici.
2. Correggere il builder Bybit nel ramo `SL_ONLY`.
3. Rendere espliciti i `missing_fields` nei warning/notifiche.
4. Ripulire i marker del profilo `trader_prova` per evitare `TP1 -> TP_HIT` nei segnali.

### File coinvolti

- `src/runtime_v2/lifecycle/entry_gate.py`
- `src/runtime_v2/lifecycle/entry_command_factory.py`
- `src/runtime_v2/execution_gateway/adapters/ccxt_bybit/order_builder.py`
- `src/runtime_v2/control_plane/formatters/templates/clean_log.py`
- `src/parser_v2/profiles/trader_prova/*`
