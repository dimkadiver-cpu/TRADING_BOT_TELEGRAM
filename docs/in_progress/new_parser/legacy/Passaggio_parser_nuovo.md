● Ti spiego il processo nuovo end-to-end, partendo da un messaggio Telegram fino all'output canonico, e il ruolo di ogni file.                                                            
                                                                                                                                                                                        
  ---                                                                                                                                                                                     
  Il viaggio di un messaggio nel nuovo sistema                                                                                                                                            
                                                                                                                                                                                          
  Telegram                                                                                                                                                                                
     ↓
  listener.py          acquisisce il testo grezzo, lo salva in raw_messages
     ↓
  router.py            prende il messaggio, capisce quale trader è, chiama il parser
     ↓
  trader_X/profile.py  estrae: cosa dice il trader (intent + dati grezzi)
     ↓
  normalizer.py        traduce in CanonicalMessage v1
     ↓
  parse_results        salva il risultato canonico
     ↓
  operation_rules      legge CanonicalMessage e decide cosa fare

  ---
  Ruolo di ogni file

  trader_X/parsing_rules.json

  È il dizionario personale del trader. Contiene i marker linguistici specifici: le parole chiave che usa quel trader per dire "compra", "stop", "tp1", "chiudi tutto" ecc. Il profilo lo
  legge all'avvio.

  trader_X/profile.py

  Fa solo due cose:
  1. Classifica il messaggio: è un segnale nuovo? Un update? Un'info?
  2. Estrae i dati grezzi: symbol, entries, stop, TP, target refs — usando il vocabolario del trader.

  Non decide più come rappresentare il risultato finale. Non costruisce targeting canonico, non mappa intent in operazioni. Produce solo dati grezzi in forma standard (TraderParseResult
  dataclass).

  rules_engine.py

  Supporto al profilo. Legge parsing_rules.json e offre metodi per matchare i marker nel testo. Il profilo lo chiama per sapere "questo testo contiene un marker di CLOSE?".

  canonical_v1/models.py  ← NUOVO

  Definisce il contratto unico. Contiene tutte le classi Pydantic:
  - CanonicalMessage — l'envelope top-level
  - SignalPayload — dati di un segnale nuovo
  - UpdatePayload + UpdateOperation — le 5 operazioni canoniche (SET_STOP, CLOSE, CANCEL_PENDING, MODIFY_ENTRIES, MODIFY_TARGETS)
  - ReportPayload + ReportEvent — eventi passati (TP_HIT, STOP_HIT, BREAKEVEN_EXIT, ecc.)
  - Targeting — come si riferisce il messaggio a una posizione aperta
  - Price + normalize_price() — normalizzazione prezzi (formati russi/europei)

  È solo struttura e validazione. Nessuna logica di estrazione.

  canonical_v1/normalizer.py  ← NUOVO

  Questo è il cuore del sistema nuovo. Riceve il TraderParseResult grezzo dal profilo e lo trasforma in CanonicalMessage.

  Fa cose come:
  - U_MOVE_STOP + new_stop_price=91500 → UpdateOperation(op_type="SET_STOP", set_stop=StopTarget(target_type="PRICE", value=91500))
  - U_CLOSE_FULL senza scope → UpdateOperation(op_type="CLOSE", close=CloseOperation(close_scope="FULL"))
  - U_EXIT_BE → ReportEvent(event_type="BREAKEVEN_EXIT")
  - U_ACTIVATION → ReportEvent(event_type="ENTRY_FILLED")
  - reply_to_message_id=12345 → Targeting(refs=[TargetRef(ref_type="REPLY", value=12345)], strategy="REPLY_OR_LINK")

  Contiene la business logic di traduzione. È il successore di canonical_v2.py.

  canonical_v1/__init__.py

  Solo export. Espone CanonicalMessage, Price e normalize per chi importa il package.

  base.py

  Definisce ParserContext (input al profilo: testo, reply_id, link estratti, trader_code) e TraderParseResult (output grezzo del profilo). Rimane invariato. È il contratto tra profilo e
  normalizer.

  registry.py

  Mappa trader_code → profilo. Il router chiede "chi gestisce il trader_a?" e registry restituisce l'istanza del profilo giusto.

  ---
  Schema con nomi file

  router.py
     │
     ├─ legge ParserContext da DB
     ├─ chiede a registry.py: quale profilo?
     │
     ▼
  trader_a/profile.py  (usa parsing_rules.json + rules_engine.py)
     │
     │  produce TraderParseResult (dataclass, grezzo)
     │  {message_type, intents, entities, target_refs, ...}
     │
     ▼
  canonical_v1/normalizer.py
     │
     │  produce CanonicalMessage (Pydantic, validato)
     │  {primary_class, signal/update/report, targeting, ...}
     │
     ▼
  router.py  (salva su DB, passa a operation_rules)

  ---
  Differenza chiave vecchio vs nuovo

  ┌───────────────────────────────────────────┬────────────────────────────────────────────────────────────────────┬─────────────────────────────────────┐
  │                                           │                              Vecchio                               │                Nuovo                │
  ├───────────────────────────────────────────┼────────────────────────────────────────────────────────────────────┼─────────────────────────────────────┤
  │ Chi decide la forma finale                │ Il profilo trader                                                  │ Il normalizer                       │
  ├───────────────────────────────────────────┼────────────────────────────────────────────────────────────────────┼─────────────────────────────────────┤
  │ Quanti contratti esistono                 │ 3 (TraderParseResult Pydantic, dataclass, actions_structured dict) │ 1 (CanonicalMessage)                │
  ├───────────────────────────────────────────┼────────────────────────────────────────────────────────────────────┼─────────────────────────────────────┤
  │ Dove vive la business logic di traduzione │ Sparsa tra profile.py e canonical_v2.py                            │ Centralizzata in normalizer.py      │
  ├───────────────────────────────────────────┼────────────────────────────────────────────────────────────────────┼─────────────────────────────────────┤
  │ Cosa fa il profilo                        │ Classifica + estrae + costruisce quasi-output finale               │ Classifica + estrae grezzo, stop    │
  ├───────────────────────────────────────────┼────────────────────────────────────────────────────────────────────┼─────────────────────────────────────┤
  │ Validazione                               │ Nessuna strutturale sull'output                                    │ Pydantic strict su CanonicalMessage │
  └───────────────────────────────────────────┴────────────────────────────────────────────────────────────────────┴─────────────────────────────────────┘

  ---
  Un esempio concreto

  Trader A manda: "стоп в бу по этому сигналу" (stop a breakeven su questo segnale, in risposta al messaggio 9876)

  profile.py estrae:
  message_type = "UPDATE"
  intents = ["U_MOVE_STOP_TO_BE"]
  entities = {}
  target_refs = [9876]  (dal reply)

  normalizer.py produce:
  CanonicalMessage(
      primary_class="UPDATE",
      parse_status="PARSED",
      targeting=Targeting(
          refs=[TargetRef(ref_type="REPLY", value=9876)],
          strategy="REPLY_OR_LINK",
          targeted=True
      ),
      update=UpdatePayload(operations=[
          UpdateOperation(
              op_type="SET_STOP",
              set_stop=StopTarget(target_type="ENTRY", value=None)
          )
      ])
  )

  Il profilo non sa nulla di StopTarget o REPLY_OR_LINK. Il normalizer sa tutto.