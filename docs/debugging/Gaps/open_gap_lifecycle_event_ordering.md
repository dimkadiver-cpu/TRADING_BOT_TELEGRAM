# Promemoria - gap aperto su ordering eventi lifecycle

Data: 2026-05-23

## Contesto

Fix applicato:

- `CANCEL_PENDING` su chain `WAITING_ENTRY` non porta piu' la chain a `CANCELLED` al momento del messaggio Telegram.
- La decisione terminale viene demandata alla conferma exchange `PENDING_ENTRY_CANCELLED_CONFIRMED`.

Questo chiude il bug immediato che causava:

- chain chiusa troppo presto;
- reply futuri sul thread non piu' targettabili;
- `MOVE_STOP_TO_BE` / update successivi finiti in `REVIEW_REQUIRED` con `no_update_target`.

## Gap ancora aperto

Resta da verificare in modo esplicito la robustezza della state machine quando eventi exchange reali arrivano in ordine concorrente o inatteso.

Il punto non e' piu' il messaggio Telegram di cancel, ma l'ordering tra eventi exchange e reconciliation.

## Casi da coprire

1. `ENTRY_FILLED` della leg 1 arriva prima di `PENDING_ENTRY_CANCELLED_CONFIRMED` della leg 2.
   Atteso:
   - la chain deve risultare `OPEN`;
   - la leg cancellata deve diventare `CANCELLED`;
   - il thread deve restare targettabile per update futuri.

2. `PENDING_ENTRY_CANCELLED_CONFIRMED` della leg 2 arriva prima di `ENTRY_FILLED` della leg 1.
   Atteso:
   - la chain non deve perdersi in uno stato terminale sbagliato se la posizione reale esiste gia' o viene consolidata subito dopo;
   - l'arrivo successivo di `ENTRY_FILLED` deve poter aprire correttamente la chain.

3. Multi-entry con piu' `ENTRY_FILLED` ravvicinati.
   Atteso:
   - `filled_entry_qty`, `open_position_qty`, `entry_avg_price` e `plan_state_json` devono restare coerenti;
   - nessuna leg deve restare con stato ambiguo.

4. `TP_FILLED` o `SL_FILLED` mentre sono ancora pendenti cancel/sync di ordini residui.
   Atteso:
   - nessuna transizione terminale deve essere sovrascritta da eventi piu' vecchi;
   - i comandi residuali non devono riportare la chain in uno stato incoerente.

5. Doppia sorgente eventi: reconciliation REST e websocket.
   Atteso:
   - idempotenza su eventi equivalenti;
   - nessun doppio avanzamento di stato.

## Layer da rivedere

- `src/runtime_v2/lifecycle/entry_gate.py`
- `src/runtime_v2/lifecycle/event_processor.py`
- `src/runtime_v2/lifecycle/workers.py`
- `src/runtime_v2/execution_gateway/event_sync.py`

## Nota importante

Il problema non va trattato come fix locale del `MOVE_STOP_TO_BE`.

Il tema aperto e':

- semantica di ownership dello stato chain;
- ordering tra `ENTRY_FILLED`, `PENDING_ENTRY_CANCELLED_CONFIRMED`, `TP_FILLED`, `SL_FILLED`;
- garanzia che una chain non diventi terminale prima che gli eventi exchange rilevanti siano stati consolidati.

## Prossimo passo consigliato

Aggiungere test di integrazione lifecycle espliciti per i casi 1 e 2 sopra, partendo dal caso reale:

- leg 1 fillata;
- leg 2 cancellata;
- update Telegram successivo sul thread originale.
