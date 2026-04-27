- Normalizare i valri come 1/2 ecc
- vedere se in operation rules, agungo le regole, tipo
    se ho tp + be allo è,ecc 
- in traduzione dal canonical message:
    - regole di esecuzione tipo "ranter" - close all se essitano, new signal (riferminero a veschhio segnale) ecc 
    - se in stop move non ce prezzo metti BE (calcolato se ce piu entry)



      Analisi dei 37 fallimenti pre-esistenti                                                                                                                                                         
                                         
  Gruppo 1 — src/operation_rules/tests/test_engine.py — 29 test                                                                                                                                   
     
  Causa: config/operation_rules.yaml ha ancora le chiavi legacy AVERAGING e ZONE come famiglie top-level in entry_split. Il loader.py ha già un validatore che le rifiuta esplicitamente:
  ValueError: entry_split.AVERAGING is deprecated; use LIMIT/MARKET canonical families
  Tutti e 29 i test del motore falliscono alla stessa riga — il config non è stato migrato alla shape canonica.

  Rischio: ALTO — il modulo operation_rules è completamente non funzionale con la config attuale. Il validatore blocca il loader prima che qualsiasi test esegua logica. È il blocco diretto per
  Step B del piano (Migrare operation_rules → usa CanonicalMessage). Va risolto come prima cosa di quella fase, non è un test da aggiornare.

  ---
  Gruppo 2 — src/parser/canonical_v1/tests/test_legacy_event_envelope_adapter.py — 3 test

  Causa: I test sono stati scritti per la shape pre-FASE 0 e usano:
  - size_hint come stringa ("1/3") invece di SizeHintRaw(raw="1/3")
  - update_payload_raw.operations che non esiste più (rimosso in FASE 0, sostituito con stop_update, close_update, ecc.)

  Rischio: BASSO — il codice è corretto. I test sono obsoleti e vanno semplicemente aggiornati alla nuova shape. Non bloccano nulla in produzione.

  ---
  Gruppo 3 — src/parser/trader_profiles/trader_d/tests/test_profile_smoke.py — 1 test

  Causa: Il test si aspetta result.message_type == "NEW_SIGNAL" ma il profilo trader_d emette "SETUP_INCOMPLETE". Divergenza tra aspettativa del test e comportamento reale del profilo
  (probabilmente il profilo è stato aggiornato senza aggiornare il test, o il test era scritto per comportamento futuro).

  Rischio: BASSO-MEDIO — non è un crash, è un mismatch semantico. Va chiarito se SETUP_INCOMPLETE è il comportamento corretto (e il test va aggiornato) o se è un bug del profilo. Da risolvere
  prima di FASE 5 (rollout trader_d).

  ---
  Gruppo 4 — src/telegram/tests/test_listener_recovery.py — 1 test

  Causa: get_messages viene chiamato anche per channel senza last_id (dovrebbe essere saltato). Bug reale nel listener di catchup.

  Rischio: MEDIO — comportamento listener non corretto in produzione sul catchup iniziale, ma fuori scope del piano parser.

  ---
  Gruppo 5 — src/telegram/tests/test_router_phase4.py — 3 test

  Cause distinte:
  - Pairlist dinamica non aggiornata dopo un nuovo segnale
  - Review queue vuota quando un update non risolto dovrebbe finirci
  - Stop price errato (59000.0 != 60000.0) su un move_stop runtime

  Rischio: MEDIO — il router phase4 ha funzionalità non ancora completamente stabili. Fuori scope del piano parser.

  ---
  Tabella rischi

  ┌────────────────────────┬─────────┬────────────────────────────────────────────────────────────┬─────────────┬──────────────────────────────────────┐
  │         Gruppo         │ N. test │                        Causa radice                        │   Rischio   │                Azione                │
  ├────────────────────────┼─────────┼────────────────────────────────────────────────────────────┼─────────────┼──────────────────────────────────────┤
  │ operation_rules engine │ 29      │ config/operation_rules.yaml ha shape legacy AVERAGING/ZONE │ ALTO        │ Migrare la config prima di Step B    │
  ├────────────────────────┼─────────┼────────────────────────────────────────────────────────────┼─────────────┼──────────────────────────────────────┤
  │ adapter tests          │ 3       │ Test scritti per shape pre-FASE 0                          │ Basso       │ Aggiornare i test in FASE 3 o FASE 6 │
  ├────────────────────────┼─────────┼────────────────────────────────────────────────────────────┼─────────────┼──────────────────────────────────────┤
  │ trader_d smoke         │ 1       │ SETUP_INCOMPLETE vs NEW_SETUP atteso                       │ Basso-Medio │ Chiarire semantica prima di FASE 5   │
  ├────────────────────────┼─────────┼────────────────────────────────────────────────────────────┼─────────────┼──────────────────────────────────────┤
  │ listener catchup       │ 1       │ Bug nel catchup per channel senza last_id                  │ Medio       │ Fix separato, fuori scope parser     │
  ├────────────────────────┼─────────┼────────────────────────────────────────────────────────────┼─────────────┼──────────────────────────────────────┤
  │ router phase4          │ 3       │ Funzionalità router non ancora stabili                     │ Medio       │ Fix separato, fuori scope parser     │
  └────────────────────────┴─────────┴────────────────────────────────────────────────────────────┴─────────────┴──────────────────────────────────────┘

  Il rischio più urgente è il Gruppo 1: operation_rules non parte affatto con la config attuale. Quando si arriverà allo Step B del piano, il primo passo sarà migrare config/operation_rules.yaml
   alla shape canonica LIMIT/MARKET.