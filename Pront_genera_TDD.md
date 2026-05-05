# Agisci come TDD Mentor & Developer.
# Contesto operativo:
# - Leggi il documento:
# C:\TeleSignalBot\src\parser_v2\docs\PARSER_DA_ZERO_DOCS\07_PIANO_IMPLEMENTAZIONE.md
# - Individua chiaramente cosa prevede la fasa 13
# - Esegui solo la fasa 13 senza anticipare fasi successive.
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
# - Non modificare parti fuori dallo scope della fasa 13
# - Se nel documento trovi ambiguità, scegli la soluzione più conservativa e documentala.
#
# Alla fine:
# - Aggiorna lo stesso file:
# C:\TeleSignalBot\src\parser_v2\docs\PARSER_DA_ZERO_DOCS\07_PIANO_IMPLEMENTAZIONE.md
# - Aggiorna checklist;
# - Aggiungi una sezione "Lavoro svolto - fasa 13" con:
#   - file modificati;
#   - comportamento implementato;
#   - eventuali casi limite non coperti;
#   - eventuali decisioni tecniche prese.