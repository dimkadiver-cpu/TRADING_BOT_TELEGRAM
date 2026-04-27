# Agisci come TDD Mentor & Developer.
# Contesto operativo:
# - Leggi il documento:
# C:\TeleSignalBot\docs\in_progress\new_parser\PIANO_IMPLEMENTAZIONE_DISAMBIGUATION_CONTEXT_RESOLUTION.md
# - Individua chiaramente cosa prevede la STEP 0 .
# - Esegui solo la STEP 0 , senza anticipare fasi successive.
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
# - Non modificare parti fuori dallo scope della STEP 0 .
# - Se nel documento trovi ambiguità, scegli la soluzione più conservativa e documentala.
#
# Alla fine:
# - Aggiorna lo stesso file:
# C:\TeleSignalBot\docs\in_progress\new_parser\PIANO_IMPLEMENTAZIONE_DISAMBIGUATION_CONTEXT_RESOLUTION.md
# - Aggiungi una sezione "Lavoro svolto - STEP 0 " con:
#   - file modificati;
#   - comportamento implementato;
#   - eventuali casi limite non coperti;
#   - eventuali decisioni tecniche prese.