❯ verifica come si potrebbe inserire in segnale nel caso del prezzo derivato dal range la sua gestione dalle regole (midlpoit, ecc)               

● Leggo prima i modelli canonici e l'operation rules per capire dove si inserisce.

  Searched for 3 patterns, read 8 files (ctrl+o to expand)

● Ho abbastanza. Traccio l'intera catena ora.

  ---
  Il range price derivation è già implementato nel signal_enrichment layer. Ecco il flusso completo:

  Flusso attuale

  1. Parser → CanonicalMessage

  signal.entry_structure = "RANGE"
  signal.entries = [
    EntryLeg(sequence=1, entry_type=LIMIT, price=65000),  # top del range
    EntryLeg(sequence=2, entry_type=LIMIT, price=63000),  # bottom del range
  ]

  2. SignalEnrichmentProcessor._apply_entry_weights() (processor.py:158)

  Per struttura RANGE + LIMIT: legge config.signal_policy.entry_split.LIMIT.range → EntryRangeConfig con due campi:
  - split_mode: "endpoints" | "firstpoint" | "lastpoint" | "midpoint"
  - weights: es. {"E1": 0.5, "E2": 0.5}

  Poi chiama _apply_range_split().

  3. _apply_range_split() (processor.py:201)

  ┌────────────┬─────────────────────────────────────────────────────────┐
  │ split_mode │                        Risultato                        │
  ├────────────┼─────────────────────────────────────────────────────────┤
  │ endpoints  │ mantiene entrambe le leg, 2 ordini limit alle estremità │
  ├────────────┼─────────────────────────────────────────────────────────┤
  │ firstpoint │ collassa a 1 leg a min(prices) (bottom)                 │
  ├────────────┼─────────────────────────────────────────────────────────┤
  │ lastpoint  │ collassa a 1 leg a max(prices) (top)                    │
  ├────────────┼─────────────────────────────────────────────────────────┤
  │ midpoint   │ collassa a 1 leg a (min+max)/2, weight=1.0              │
  └────────────┴─────────────────────────────────────────────────────────┘

  Per i tre casi non-endpoints: prende la leg con sequence minimo, sostituisce il prezzo con quello derivato, weight=1.0 — la seconda leg viene
  eliminata.

  4. ExecutionPlanBuilder.build() (execution_plan.py:19)

  Riceve le leg risultanti (1 o 2) e costruisce il piano con quei prezzi. Non distingue RANGE da ONE_SHOT — tratta le leg come lista.

  5. Configurazione in operation_config.yaml

  signal_policy:
    entry_split:
      LIMIT:
        range:
          split_mode: midpoint     # ← qui si sceglie la regola
          weights:
            E1: 0.5
            E2: 0.5

  Per endpoints i weights dividono il risk budget tra le due leg; per gli altri split_mode il weight finale è sempre 1.0 (gestito da
  _apply_range_split).

  ---
  Problemi attuali

  1. entry_structure non aggiornata dopo collasso

  Quando midpoint/firstpoint/lastpoint collassano a 1 leg, EnrichedSignalPayload.entry_structure rimane "RANGE". Ma la struttura effettiva è
  ONE_SHOT. Qualsiasi codice downstream che legge entry_structure per decidere il comportamento (es. cancel_averaging_pending_after, risk
  rebalance, cancellazione pending) opera su un'informazione sbagliata.

  Fix naturale: aggiornare entry_structure nell'EnrichedSignalPayload dopo il collasso:

  if split_mode != "endpoints" and len(collapsed_legs) == 1:
      enriched_signal.entry_structure = "ONE_SHOT"

  2. Nessun EnrichmentLogEntry per il range collapse

  La derivazione del prezzo (es. midpoint calcolato) è invisibile nei log. Se un ordine viene aperto a 64,000 e il range originale era
  63,000–65,000, non c'è traccia nel log di come si è arrivati a quel prezzo.

  Fix naturale: aggiungere una EnrichmentLogEntry:
  EnrichmentLogEntry(
      check="range_price_derived",
      original=f"{min_price}-{max_price}",
      result=str(target),
      detail=split_mode,
  )

  3. endpoints mode: semantica di esecuzione non definita

  Con endpoints si piazzano 2 ordini limit simultanei alle estremità del range. Il ExecutionPlanBuilder li tratta come 2 leg indipendenti (stesso
  schema di TWO_STEP). Ma il comportamento quando una leg si riempie è governato da rebuild_policy e cancel_averaging_pending_after del
  management_plan — che non distingue "stavo in una zona" da "era un averaging classico".

  Domanda da rispondere prima di usare endpoints in produzione: la seconda leg deve rimanere attiva dopo il fill della prima (averaging in zona) o
  va cancellata?

  ---
  Dove aggiungere una nuova regola

  Se vuoi aggiungere un nuovo split_mode (es. weighted_midpoint che ponderi verso SL, o tp1_proximity che sceglie la leg più vicina a TP1), il
  punto di inserimento è:

  - EntryRangeConfig.split_mode — aggiungi il literal type (models.py:26)
  - _apply_range_split() — aggiungi il branch di calcolo (processor.py:215)
  - Config YAML del trader — imposta il nuovo mode

  Nessun cambio al parser, al modello canonico, o all'execution plan builder.
● Esatto. I gap 1 e 2 sono fix di codice diretti. Il gap 3 richiede prima una decisione di design. Propongo questo ordine:

  Gap 1 — entry_structure non aggiornata dopo collasso → fix in processor.py, 2 righe
  Gap 2 — nessun log del prezzo derivato → aggiunta EnrichmentLogEntry in _apply_entry_weights
  Gap 3 — semantica endpoints da definire → decisione prima del codice