# Handoff — sessione 2026-06-07 / trader risk hint integration

---

## Cosa è stato fatto

Implementato il wiring end-to-end di `use_trader_risk_hint` nel runtime v2. Il flag era già configurato e caricato, ma `RiskCapacityEngine` non lo consumava mai — gap ora chiuso.

---

## Step completati (5 task, subagent-driven)

| Task | Commit | Contenuto |
|---|---|---|
| 1 | `d239de6` | `risk_hint_range_mode` in `RiskConfig` + YAML config |
| 2 | `8c5ff0f` | `risk_hint` in `EnrichedSignalPayload` + propagazione dal canonical |
| 3 | `eed8671` ⚠️ | Logica reduce-only in `RiskCapacityEngine` + `RiskDecision.hint_applied` |
| 4 | `eb1fac5` | `extra_plan_metadata` in `ExecutionPlanBuilder.build()` |
| 5 | `ff67dc3` | Wiring in `entry_gate.py`: `risk_hint_applied` + `range_derivation` → `plan_state_json` |

> ⚠️ Commit `eed8671` ha messaggio "123" (errore subagent). Codice corretto, storia sporca.

---

## File toccati

```
src/runtime_v2/signal_enrichment/models.py        ← RiskConfig + EnrichedSignalPayload
src/runtime_v2/signal_enrichment/processor.py     ← propaga risk_hint
src/runtime_v2/lifecycle/risk_capacity.py         ← logica reduce-only + hint_applied
src/runtime_v2/lifecycle/execution_plan.py        ← extra_plan_metadata param
src/runtime_v2/lifecycle/entry_gate.py            ← assembla extra_plan, chiama builder
config/operation_config.yaml                       ← aggiunto risk_hint_range_mode
config/traders/trader_3.yaml                      ← aggiunto risk_hint_range_mode
tests/runtime_v2/signal_enrichment/test_models.py
tests/runtime_v2/signal_enrichment/test_processor_signal.py
tests/runtime_v2/lifecycle/test_risk_capacity.py
tests/runtime_v2/lifecycle/test_execution_plan.py
tests/runtime_v2/lifecycle/test_entry_gate.py
```

---

## Stato attuale

```
pytest tests/runtime_v2/ -q
→ 1012 passed, 38 failed (tutti pre-existing), 6 skipped
```

La feature è completa e verificata end-to-end. `plan_state_json["risk_hint_applied"]` è disponibile su `ops_trade_chains` per downstream use.

---

## Design applicato

- **Reduce-only**: `effective_risk = min(config_risk, hint_value)`. Se hint >= config, nessun `hint_applied`.
- **`risk_usdt_fixed` skip**: hint percentuale non applicabile a budget fixed-USDT.
- **Range mode**: `risk_hint_range_mode: min_value | max_value | midpoint` risolve hint a range.
- **`plan_state_json` shape** quando hint applicato:
  ```json
  {
    "risk_hint_applied": {
      "hint_used": true,
      "hint_raw": "1%",
      "hint_effective_pct": 1.0,
      "configured_risk_pct": 2.0,
      "effective_risk_pct": 1.0
    }
  }
  ```
- **Clean-log display**: deliberatamente fuori scope — decide sessione separata.

---

## Rischi aperti

1. Commit `eed8671` messaggio "123" — cleanup opzionale con `git rebase -i`.
2. `risk_hint` non estratto da `trader_3` (il profilo non emette hint) — noto, non bloccante.
3. 38 test pre-existing falliscono (non legati a questa feature).

---

## Prossimo prompt consigliato

```
Inizia Step B: migra src/operation_rules/ per usare CanonicalMessage
invece dei modelli legacy in src/parser/models/.
Leggi prima CLAUDE.md, poi i file in src/operation_rules/.
```

---

## Spec e piano

- Spec: `docs/superpowers/specs/2026-06-07-trader-risk-hint-integration-design.md`
- Piano: `docs/superpowers/plans/2026-06-07-trader-risk-hint-integration.md`
