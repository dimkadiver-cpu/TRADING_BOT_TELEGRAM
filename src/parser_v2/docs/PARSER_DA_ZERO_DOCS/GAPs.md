Ho letto tutta la cartella PARSER_DA_ZERO_DOCS e ho confrontato i contratti con il codice attivo in src/parser, src/telegram, src/
  validation, src/operation_rules, src/target_resolver.

  Problemi Principali

  - Bloccante: UPDATE/PARTIAL senza payload è incompatibile con le validazioni schema. Il caso multi-ref misto produce primary_class=UPDATE,
    parse_status=PARTIAL e solo warning, senza update.operations né targeted_actions, ma il contratto dice che un UPDATE deve avere almeno
    una delle due. Riferimenti: src/parser_v2/docs/PARSER_DA_ZERO_DOCS/08_MULTI_REF_TARGETED_ACTIONS.md:180, src/parser_v2/docs/
    PARSER_DA_ZERO_DOCS/08_MULTI_REF_TARGETED_ACTIONS.md:410, src/parser_v2/docs/PARSER_DA_ZERO_DOCS/05_CANONICAL_MESSAGE.md:328, src/
    parser_v2/docs/PARSER_DA_ZERO_DOCS/07_PIANO_IMPLEMENTAZIONE.md:443.
  - Bloccante: precedenza UPDATE vs REPORT non deterministica. La pipeline semplice dice signal -> update -> report -> info, ma altri file
    dicono che SL_HIT può far restare primary_class=REPORT anche con CLOSE_FULL. Va scelta una regola unica. Riferimenti: src/parser_v2/
    docs/PARSER_DA_ZERO_DOCS/01_PIPELINE_COMPLETA.md:195, src/parser_v2/docs/PARSER_DA_ZERO_DOCS/04_CLASSIFICAZIONE_MESSAGGIO.md:310, src/
    parser_v2/docs/PARSER_DA_ZERO_DOCS/05_CANONICAL_MESSAGE.md:402, src/parser_v2/docs/PARSER_DA_ZERO_DOCS/05_CANONICAL_MESSAGE.md:430.
  - Bloccante: enum/payload per MODIFY_ENTRIES incompleto. ADD_ENTRY e REENTER vengono mappati a MODIFY_ENTRIES mode=ADD/REENTER, ma
    12_ENUMS definisce solo ModifyEntryMode = MARKET_NOW/UPDATE_PRICE/REMOVE/UNKNOWN. Manca un enum separato tipo ModifyEntriesOperationMode
    = ADD/REENTER/MARKET_NOW/UPDATE_PRICE/REMOVE. Riferimenti: src/parser_v2/docs/PARSER_DA_ZERO_DOCS/05_CANONICAL_MESSAGE.md:189, src/
    parser_v2/docs/PARSER_DA_ZERO_DOCS/07_PIANO_IMPLEMENTAZIONE.md:348, src/parser_v2/docs/PARSER_DA_ZERO_DOCS/12_ENUMS_E_CONSTANTI.md:194,
    src/parser_v2/docs/PARSER_DA_ZERO_DOCS/09_MODIFY_ENTRY_MODE_MARKERS.md:68.
  - Gap: INVALIDATE_SETUP è operation type ma non ha contratto payload. UpdateOperation cita invalidate_setup: InvalidateSetupOperation, ma
    nei documenti non c’è lo schema di InvalidateSetupOperation né la sua validazione. Riferimenti: src/parser_v2/docs/
    PARSER_DA_ZERO_DOCS/05_CANONICAL_MESSAGE.md:163, src/parser_v2/docs/PARSER_DA_ZERO_DOCS/03_INTENTS_ENTITIES_MINIME.md:210, src/
    parser_v2/docs/PARSER_DA_ZERO_DOCS/12_ENUMS_E_CONSTANTI.md:262.
  - Incompatibilità interna: REPORT_RESULT viene mappato in due modi diversi. Un file dice REPORT_RESULT -> ReportPayload con event_type
    minimale, ma 12_ENUMS dice che REPORT_RESULT è escluso dagli eventi e vive in ReportPayload.result. Riferimenti: src/parser_v2/docs/
  - Gap: ParsedIntent.status usa un enum non definito. 03 usa IntentEvidenceStatus, ma 12 definisce solo EvidenceStatus. Va deciso se è lo
    stesso enum o un enum separato. Riferimenti: src/parser_v2/docs/PARSER_DA_ZERO_DOCS/03_INTENTS_ENTITIES_MINIME.md:80, src/parser_v2/
    docs/PARSER_DA_ZERO_DOCS/12_ENUMS_E_CONSTANTI.md:46.
  - Ambiguità: update.operations e targeted_actions sono esclusivi in modo diverso tra i file. 05 vieta entrambi solo “per la stessa
    azione”, mentre 08 dice “mai metà in uno, metà nell’altro”. Serve una regola unica per messaggi con un’azione targettata e una message-
    wide. Riferimenti: src/parser_v2/docs/PARSER_DA_ZERO_DOCS/05_CANONICAL_MESSAGE.md:111, src/parser_v2/docs/
    PARSER_DA_ZERO_DOCS/08_MULTI_REF_TARGETED_ACTIONS.md:342.
  - Gap architetturale: il multi-ref richiede associazione riga/link/intent, ma il contratto intermedio non la garantisce. L’algoritmo di 08
    usa line_intents e match_intents_in_line, però il runtime in 11 passa al translator un ParsedMessage già aggregato. Serve conservare
    line_index, span, target hints per frammento o introdurre un oggetto intermedio. Riferimenti: src/parser_v2/docs/
    PARSER_DA_ZERO_DOCS/08_MULTI_REF_TARGETED_ACTIONS.md:206, src/parser_v2/docs/
    PARSER_DA_ZERO_DOCS/11_ARCHITETTURA_UNIVERSALE_PARSER.md:537.
  - Ambiguità risolta nel runtime v2: marker `info` valido interrompe il parsing operativo e produce `INFO` direttamente. Questo chiude
    il conflitto tra `info_markers` e gli estrattori operativi. Riferimenti: src/parser_v2/core/runtime.py:78, src/parser_v2/docs/
    PARSER_DA_ZERO_DOCS/04_CLASSIFICAZIONE_MESSAGGIO.md:246, src/parser_v2/docs/PARSER_DA_ZERO_DOCS/06_1_SEMANTIC_MARKERS_COMPLETO.md:719.
  - Incompatibilità con codice attivo: parser_v2 non è drop-in. I docs dicono “nessun adapter” e downstream da riscrivere, ma il router
    attuale chiama ancora parse_message() e lavora su TraderParseResult, validazione legacy e canonical v1. Riferimenti: src/parser_v2/docs/
    PARSER_DA_ZERO_DOCS/00_SCOPE_E_DECISIONI.md:49, src/telegram/router.py:270, src/parser/trader_profiles/base.py:24, src/validation/
    coherence.py:101, src/target_resolver/resolver.py:28.

  Nota di fondo
  La causa principale è che i documenti sono buoni come direzione, ma non sono ancora un contratto implementabile: enum, payload,
  validazioni e regole di precedenza sono duplicati e alcuni casi edge sono descritti come output anche quando violano lo schema.

  Validazione

  - Ho letto README, tutti i file in src/parser_v2/docs/PARSER_DA_ZERO_DOCS, e i punti codice collegati in src/parser, src/telegram, src/
    validation, src/operation_rules, src/target_resolver.
  - Non ho eseguito test perché questa era una review documentale/contrattuale e non ci sono modifiche da validare.

  Primary signal status: met.
  Secondary signal status: verifica tramite lettura file e rg; nessun test eseguito.
  Documentazione: non aggiornata.
  Commit message suggerito se si correggono i docs: docs(parser-v2): align contracts and resolve parser v2 ambiguities

─ Worked for 3m 16s ────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────


› breve proposta per risolvere (tranne  Ambiguità: marker #admi  )


• Proposta breve:

  1. Schema UPDATE/PARTIAL
     Consentire UPDATE/PARTIAL senza payload operativo solo per casi non eseguibili.
     Aggiungere campo/flag:
     non_executable_reason = "multi_ref_mixed_intents_not_supported".
     Oppure più semplice: classificarlo INFO/UNCLASSIFIED è sbagliato, quindi meglio allentare la validation per PARTIAL.
  2. Precedenza UPDATE vs REPORT
     Regola unica:
     SIGNAL domina tutto.
     UPDATE domina REPORT, tranne casi dichiarati report_absorbs_update in rules.json, es. SL_HIT + CLOSE_FULL.
     Quindi l’eccezione non sta hardcoded nello schema, ma nelle rules.
  3. MODIFY_ENTRIES mode
     Separare gli enum:
     ModifyEntryMode = MARKET_NOW | UPDATE_PRICE | REMOVE | UNKNOWN
     ModifyEntriesOperationMode = MARKET_NOW | UPDATE_PRICE | REMOVE | ADD | REENTER | UNKNOWN
     ADD_ENTRY e REENTER non diventano mode di MODIFY_ENTRY, ma solo mode operativi nel canonical.
  4. INVALIDATE_SETUP payload
     Definire:

     class InvalidateSetupOperation(BaseModel):
         reason_text: str | None = None

     Validation: op_type=INVALIDATE_SETUP richiede solo invalidate_setup.

  5. REPORT_RESULT
     Decisione unica:
     REPORT_RESULT non è ReportEvent.
     Va in:

     ReportPayload.result: ReportResult | None

     ReportEventType resta solo ENTRY_FILLED | TP_HIT | SL_HIT | EXIT_BE.

  6. ParsedIntent.status
     Usare EvidenceStatus anche per ParsedIntent.status.
     Eliminare il nome IntentEvidenceStatus dai docs.
  7. update.operations vs targeted_actions
     Regola consigliata:
     se esiste anche una sola azione con target esplicito per riga/link, tutte le azioni operative vanno in targeted_actions.
     Niente mix nello stesso messaggio.
  8. Multi-ref senza InstructionUnit
     Aggiungere a ParsedIntent:

     span_start: int | None
     span_end: int | None
     line_index: int | None

     Così CanonicalTranslator può associare intent e link per riga senza introdurre subito InstructionUnit.

  9. Compatibilità con codice attivo
     Tenere parser_v2 isolato.
     Primo step implementativo: solo modelli + translator + test.
     Integrazione router/downstream dopo, con feature flag esplicito, non sostituendo subito parse_message() legacy.

## Decisioni approvate (chiusura gap)

Decisioni confermate:

1. **A** — `UPDATE/PARTIAL` senza payload operativo è consentito solo nei casi non eseguibili (es. `multi_ref_mixed_intents_not_supported`).
2. **A** — precedenza unica: `SIGNAL > UPDATE > REPORT > INFO`; eventuali particolarità sono diagnostiche, non cambiano `primary_class`.
3. **A** — separazione enum tra intent-level (`ModifyEntryMode`) e operation-level (`ModifyEntriesOperationMode`).
4. **A** — `INVALIDATE_SETUP` ha payload minimo esplicito (`reason_text` opzionale).
5. **Scelta finale** — `REPORT_RESULT` identifica il **messaggio/segmento di risultato**, ma nel canonical vive in `report.result` (non in `report.events`).
6. **A** — `ParsedIntent.status` usa lo stesso enum `EvidenceStatus`.
7. **A** — nessun mix: se esiste target esplicito, tutte le operazioni vanno in `targeted_actions`.
8. **A** — ponte pre-`InstructionUnit`: aggiungere metadati minimi (`line_index`, `span_start`, `span_end`) agli intent.
9. **B** — integrazione diretta: parser_v2 sostituisce il flusso legacy senza fase intermedia a feature flag.
