# Agisci come TDD Mentor & Developer.
# Contesto operativo:
# - Leggi il documento:
# C:\TeleSignalBot\docs\in_progress\new_parser\PARSER_REDESIGN_SPEC_V1.md
# - Individua chiaramente cosa prevede la Fasa 6.
# - Esegui solo la Fasa 6, senza anticipare fasi successive.
#
# Metodo di lavoro:
# - Prima analizza il problema e il comportamento atteso.
# - Poi scrivi o aggiorna i test minimi necessari prima del codice.
# - Esegui il ciclo Red → Green → Refactor:
#   1. Red: identifica il test che deve fallire.
#   2. Green: implementa la soluzione più semplice per farlo passare.
#   3. Refactor: migliora il codice senza cambiare il comportamento.
#
# Vincoli:
# - Non introdurre refactor grandi o funzionalità non richieste.
# - Mantieni le modifiche piccole, verificabili e motivate.
# - Non modificare parti fuori dallo scope della Fasa 6.
# - Se nel documento trovi ambiguità, scegli la soluzione più conservativa e documentala.
#
# Alla fine:
# - Aggiorna lo stesso file:
#  C:\TeleSignalBot\docs\in_progress\new_parser\PARSER_REDESIGN_SPEC_V1.md
# - Aggiorna checklist;
# - Aggiungi una sezione "Lavoro svolto - Fasa 6" con:
#   - file modificati;
#   - comportamento implementato;
#   - eventuali casi limite non coperti;
#   - eventuali decisioni tecniche prese.