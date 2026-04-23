  Ordine consigliato

  1. Congelare la shape minima proposta
     File di riferimento:
      - /C:/TeleSignalBot/docs/in_progress/new_parser/PROPOSTA_SCHEMA_UNICO_EVENTI_V1.md
      - /C:/TeleSignalBot/docs/in_progress/new_parser/parser_event_envelope_v1.py

     Decisione da chiudere:
      - questa shape diventa il contratto parser-side ufficiale
      - niente nuovi campi top-level oltre quelli definiti
      - i campi utili da preservare restano: risk_hint, size_hint, close_fraction, reported_result, price/level report
  2. Scrivere la tabella di mapping legacy -> envelope v1
     Questo è il prossimo documento/codice più importante.
     Dobbiamo definire, per ogni campo legacy attuale, dove finisce nel nuovo envelope.

     Esempi:
      - entities.entry_structure -> signal_payload_raw.entry_structure
      - entities.entries -> signal_payload_raw.entries
      - entities.stop_loss -> signal_payload_raw.stop_loss.price
      - entities.new_stop_level -> update_payload_raw.operations[*].set_stop
      - entities.close_fraction -> update_payload_raw.operations[*].close.close_fraction
      - reported_results -> report_payload_raw.reported_result
  3. Implementare un adapter centrale TraderParseResult -> TraderEventEnvelopeV1
     Non toccare ancora i parser.
     Prima facciamo un adapter unico che prende l’output legacy attuale e lo converte nel nuovo envelope minimo.

     Questo ci dà subito:
      - un solo punto di compatibilità
      - niente fallback sparsi
      - base per test reali
  4. Aggiornare il normalizer a leggere l’envelope v1
     Il normalizer deve preferire il nuovo envelope.
     Solo se manca, può usare temporaneamente l’adapter legacy.

     In pratica:
      - parser legacy
      - adapter -> TraderEventEnvelopeV1
      - normalizer -> CanonicalMessage v1
  5. Risolvere il bug concreto rimasto sui TWO_STEP
     Appena esiste l’adapter, la prima fix pratica è questa:
      - far sì che le entry di trader_c confluiscano in signal_payload_raw.entries
      - poi il normalizer legge solo quello

     Questo dovrebbe chiudere i 13 errori residui molto più pulitamente che con altri fallback locali.
  6. Solo dopo, migrare i parser trader uno per volta
     Quando adapter e normalizer sono stabili:
      - trader_3
      - trader_b
      - trader_c
      - trader_d
      - trader_a o nell’ordine che preferisci operativamente

     Ogni parser smette gradualmente di produrre il legacy e produce direttamente l’envelope v1.

  Quello che farei subito adesso
  Il prossimo task più sensato è questo:

  - creare un file tipo docs/in_progress/new_parser/MAPPING_LEGACY_TO_EVENT_ENVELOPE_V1.md
  - oppure direttamente un adapter in src/parser/...

  Se vuoi minimizzare il rischio, io farei prima il documento di mapping.
  Se vuoi muoverti più veloce, farei direttamente l’adapter centrale.

  La mia raccomandazione
  Il miglior prossimo step è:

  1. scrivere il mapping legacy -> TraderEventEnvelopeV1
  2. implementare l’adapter centrale
  3. usare quell’adapter per chiudere subito i residui trader_c

  Così fai insieme:

  - pulizia architetturale
  - fix concreta su dati reali

  Primary signal status
  La fase di progettazione del contratto parser-side è pronta per passare al mapping operativo.

  Secondary signal status
  Abbiamo già:

  - proposta documentale
  - shape Python concreta
  - conferma dai casi reali che i campi chiave da preservare sono corretti

  Messaggio commit suggerito per il prossimo step:
  docs: map legacy trader parse result fields to parser event envelope v1

  Aggiornamento 2026-04-22
  Step 2 completato.
  Documento disponibile in:
  - /C:/TeleSignalBot/docs/in_progress/new_parser/Contratto_output_dat_da_parser/MAPPING_LEGACY_TO_EVENT_ENVELOPE_V1.md
