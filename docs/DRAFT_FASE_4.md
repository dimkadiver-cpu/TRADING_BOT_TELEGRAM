# BOZZA — Fase 4: Operation Rules + Target Resolver

> **Stato:** BOZZA per brainstorm — non implementare prima di revisione con l'utente.
> **Data:** 2026-03-24

---

## Contesto

La Fase 4 è il ponte tra parser/validazione (fatto) e Sistema 1 (freqtrade live, Fase 5).

Cosa c'è già:
- ✓ Layer 3 — Validazione coerenza (`src/validation/coherence.py`, 25 test)
- ✗ Layer 4 — Operation rules — **da fare**
- ✗ Layer 5 — Target resolver — **da fare**

---

## Flusso completo Fase 4

```
TraderParseResult (validated, status=VALID)
      ↓
Layer 4 — Operation rules
  → aggiunge parametri esecutivi al segnale
  → regole configurabili per trader e globali (YAML)
      ↓
OperationalSignal (enriched)
      ↓
Layer 5 — Target resolver
  → risolve target_ref in position_id concreti
  → STRONG: reply-chain / link / explicit ID
  → SYMBOL: cerca posizioni aperte trader + symbol
  → GLOBAL: scope trader-wide
      ↓
ResolvedSignal (pronto per Sistema 1)
```

---

## Layer 4 — Operation rules

### Responsabilità

Non modifica il parsing — aggiunge parametri operativi di esecuzione.

Il parser dice "cosa dice il trader". Le operation rules dicono "come noi eseguiamo operativamente quel segnale".

### Config proposta

```
config/operation_rules.yaml               # regole globali (default)
config/trader_rules/{trader_id}.yaml      # override per trader specifico
```

Merge: trader override > global default. Campi non specificati nel trader → ereditati dal global.

### Parametri prodotti per NEW_SIGNAL

| Parametro | Tipo | Descrizione |
|---|---|---|
| `position_size_pct` | float | % portfolio da allocare alla posizione |
| `position_size_usdt` | float (opz.) | size fissa alternativa a % |
| `entry_split` | list[float] | distribuzione size sulle entries (somma = 1.0) |
| `leverage` | int | leva da usare |
| `max_concurrent_same_symbol` | int | max posizioni aperte sullo stesso symbol |
| `max_open_positions` | int | max posizioni aperte totali per trader |

### Regole tipiche da implementare

| Regola | Quando | Effetto |
|---|---|---|
| ZONE split | `entry_type = ZONE` | split configurabile tra min e max (default 50/50) |
| AVERAGING split | `entry_type = AVERAGING` | size uniforme per entry oppure decrescente |
| Global risk limit | sempre | max X% portfolio per operazione |
| Symbol duplicate check | NEW_SIGNAL | blocca se già in posizione sullo stesso symbol |
| Max open positions | NEW_SIGNAL | blocca se troppe posizioni aperte |

### Per UPDATE

Passthrough — operation rules non aggiunge parametri. Conferma solo `is_actionable = True`.

### Output: OperationalSignal

```python
class OperationalSignal(BaseModel):
    """TraderParseResult + parametri esecutivi."""

    # --- dati dal parser (passthrough) ---
    parse_result_id: int
    trader_id: str
    message_type: str                    # NEW_SIGNAL | UPDATE
    entities: dict[str, Any]
    intents: list[str]
    target_ref: dict[str, Any] | None
    confidence: float

    # --- parametri esecutivi (aggiunti da operation rules) ---
    position_size_pct: float | None = None
    position_size_usdt: float | None = None
    entry_split: list[float] | None = None
    leverage: int | None = None

    # --- gate results ---
    is_blocked: bool = False             # True se una regola blocca l'esecuzione
    block_reason: str | None = None      # "max_open_positions_reached", ecc.

    # --- audit ---
    applied_rules: list[str] = []        # quali regole sono state applicate
    warnings: list[str] = []
```

---

## Layer 5 — Target resolver

### Responsabilità

Risolve `target_ref` in position_id concreti dal DB. Non inventa nulla — solo risolve.

### Casi di risoluzione

| target_ref.kind | method | Logica |
|---|---|---|
| `STRONG` | `REPLY` | parse_result del segnale padre (via reply_to_message_id) |
| `STRONG` | `TELEGRAM_LINK` | cerca `extracted_links` nel DB parse_results |
| `STRONG` | `EXPLICIT_ID` | cerca per `ref` come parse_result_id |
| `SYMBOL` | — | cerca parse_results del trader con quel symbol, status aperto |
| `GLOBAL` | `all_long` | tutte posizioni long aperte del trader |
| `GLOBAL` | `all_short` | tutte posizioni short aperte del trader |
| `GLOBAL` | `all_positions` | tutte posizioni aperte del trader |

### Output: ResolvedTarget

```python
@dataclass(slots=True)
class ResolvedTarget:
    kind: Literal["STRONG", "SYMBOL", "GLOBAL"]
    position_ids: list[int]       # parse_result_id dei segnali originali risolti
    unresolved: bool              # True se non è stato possibile risolvere
    reason: str | None            # motivo se unresolved (es. "no_open_position_for_symbol")
```

### Cosa serve per risolvere: accessor posizioni aperte

Il resolver deve sapere quali posizioni sono "aperte". Due opzioni:

**Opzione A (leggera — proposta iniziale):**
- Legge da `parse_results` direttamente
- NEW_SIGNAL non chiusi = posizioni aperte
- Accessor in `src/storage/positions_query.py` che astrae la query
- Quando arriva Sistema 1, si sostituisce l'accessor senza toccare il resolver

**Opzione B (robusta):**
- Crea tabella `positions` con lifecycle esplicito (OPEN → CLOSED)
- Più lavoro, ma fonte di verità unica

**Pro Opzione A:** niente tabella nuova, veloce da implementare, sufficiente per iniziare.
**Pro Opzione B:** necessaria comunque per Sistema 1 — anticiparla evita refactor.

**Da decidere in brainstorm.**

---

## Stato `src/execution/` esistente — NON TOCCARE

I moduli `src/execution/` lavorano ancora sul vecchio formato `ACT_*` (legacy):

| File | Stato | Note |
|---|---|---|
| `update_planner.py` | ⚠ legacy ACT_* | usa `actions: list[str]` con `ACT_MOVE_STOP_LOSS` ecc. — non compatibile con Intent-based |
| `update_applier.py` | ⚠ da valutare | applica `StateUpdatePlan` — potenzialmente riusabile |
| `state_machine.py` | da valutare | lifecycle posizioni — Fase 5 |
| `risk_gate.py` | da leggere | potrebbe informare operation rules |
| `planner.py` | stub `# TODO` | da ignorare |

**Non toccare in Fase 4.** La scelta della struttura di output di Layer 4/5 influenzerà come questi moduli si integrano in Fase 5.

---

## Ordine di sviluppo proposto

```
Step 12 — Operation rules
  config/operation_rules.yaml                # schema e valori default
  src/operation_rules/__init__.py
  src/operation_rules/models.py              # OperationalSignal (Pydantic v2)
  src/operation_rules/loader.py              # carica e merge config YAML
  src/operation_rules/engine.py              # applica regole a TraderParseResult
  src/operation_rules/tests/test_engine.py
  src/operation_rules/tests/test_loader.py

Step 13 — Target resolver
  src/target_resolver/__init__.py
  src/target_resolver/models.py              # ResolvedTarget
  src/target_resolver/resolver.py            # logica risoluzione per kind/method
  src/storage/positions_query.py             # accessor posizioni aperte (Opzione A)
  src/target_resolver/tests/test_resolver.py

Step 14 — Integrazione nel flusso
  Il Router, dopo validation_status=VALID:
    → chiama operation_rules.engine.apply()
    → chiama target_resolver.resolver.resolve()
    → salva risultato (dove? estende parse_results o nuova tabella?)
```

---

## Domande aperte per brainstorm

1. **Opzione A o B** per le posizioni aperte? (accessor su parse_results vs tabella positions)
2. **Output Fase 4 in DB:** estendere `parse_results` con campi esecutivi, o nuova tabella `operational_signals`?
3. **Operation rules specifiche:** quali regole servono subito per i tuoi trader? Size, leverage, split — hai già valori in testa?
4. **Fase 4 prima o dopo channels.yaml live?** Il codice è indipendente, ma test realistici servono dati veri.
5. **`src/execution/` update_planner**: va riscritto per Intent-based, o si butta e si ripartisce in Fase 5?
6. **Gate bloccante o solo warning?** Se una regola (es. max positions) blocca, l'OperationalSignal ha `is_blocked=True` e non si esegue — oppure si logga solo un warning e si esegue comunque?
