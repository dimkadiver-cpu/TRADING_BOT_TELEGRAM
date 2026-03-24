---
name: fase4-operation-rules-target-resolver
description: Usa questa skill quando devi progettare, implementare o modificare i Layer 4 (Operation Rules) e Layer 5 (Target Resolver) — i layer tra validazione coerenza e Sistema 1 (execution). Copre modelli, config YAML, engine, resolver, e integrazione nel flusso.
---

# Obiettivo

Implementare i due layer che trasformano un TraderParseResult validato in un segnale operativo pronto per l'esecuzione: parametri esecutivi (size, leverage, split) e risoluzione target concreti.

# Quando usarla

- implementazione di operation_rules o target_resolver
- aggiunta/modifica regole operative per un trader
- modifica alla logica di risoluzione target_ref → position_id
- debug di segnali bloccati o target non risolti
- integrazione di Fase 4 nel Router o in un orchestratore

# Documento di riferimento

`docs/DRAFT_FASE_4.md` — contiene design completo, flusso, modelli, ordine di sviluppo e domande aperte.

# Flusso

```
TraderParseResult (validated, VALID)
      ↓
Operation rules engine
  → carica config/operation_rules.yaml + config/trader_rules/{trader_id}.yaml
  → merge: trader override > global default
  → applica regole (size, leverage, split, gate checks)
  → produce OperationalSignal
      ↓
Target resolver
  → legge target_ref da TraderParseResult
  → risolve in position_ids concreti (accessor su parse_results o tabella positions)
  → produce ResolvedTarget
      ↓
Output pronto per Sistema 1
```

# File coinvolti (piano)

```
config/
├── operation_rules.yaml                    # regole globali default
└── trader_rules/
    └── {trader_id}.yaml                    # override per trader

src/operation_rules/
├── __init__.py
├── models.py                               # OperationalSignal (Pydantic v2)
├── loader.py                               # carica e merge config YAML
├── engine.py                               # applica regole a TraderParseResult
└── tests/
    ├── test_engine.py
    └── test_loader.py

src/target_resolver/
├── __init__.py
├── models.py                               # ResolvedTarget
├── resolver.py                             # risoluzione per kind/method
└── tests/
    └── test_resolver.py

src/storage/
└── positions_query.py                      # accessor posizioni aperte
```

# Convenzioni obbligatorie

- `from __future__ import annotations` in ogni file
- Pydantic v2 per tutti i modelli (OperationalSignal)
- dataclass per strutture leggere (ResolvedTarget)
- type hints ovunque, niente `Any` salvo dove documentato
- config YAML con schema chiaro e valori default ragionevoli
- test che coprono: regole applicate, gate bloccanti, target risolti/non risolti

# Confini — cosa NON toccare

- `src/validation/coherence.py` — Layer 3 è a monte, stabile
- `src/execution/` — Fase 5, non modificare
- `src/exchange/` — Fase 5, non modificare
- `src/parser/` — il parser produce, non consuma regole operative
- `src/storage/raw_messages.py`, `src/storage/parse_results.py` — storage layer stabile

# Ordine di implementazione

```
Step 12 — Operation rules (models → loader → engine → test)
Step 13 — Target resolver (models → accessor → resolver → test)
Step 14 — Integrazione nel flusso (Router o orchestratore)
```

Non saltare step. Non iniziare Step 13 prima che Step 12 sia testato.

# Domande aperte (da risolvere in brainstorm)

1. Accessor posizioni: query su parse_results (Opzione A) o tabella positions dedicata (Opzione B)?
2. Output in DB: estendere parse_results o nuova tabella operational_signals?
3. Gate bloccante: `is_blocked=True` ferma tutto, o solo warning?
4. Relazione con `src/execution/update_planner.py` (legacy ACT_*): riscrivere o buttare in Fase 5?
