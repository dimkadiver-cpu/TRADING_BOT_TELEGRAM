# ROADMAP

## Phase status
- Fase 0: COMPLETATA
- Fase 1: COMPLETATA
- Fase 2: COMPLETATA (schema parser persistence allineato)
- Fase 3: VALIDATA
- Fase 4: IMPLEMENTATA (in validazione estesa)
- Fase 5: IN ANALISI
- Fase 6: IN ANALISI
- Fase 7+: NON INIZIATE

---

## Fase 2. Database e persistenza
Stato: COMPLETATA

Completato:
- tabelle parser minime operative
- persistenza `raw_messages`
- persistenza `parse_results`
- colonna additiva `parse_result_normalized_json`
- compatibilita mantenuta con campi legacy

---

## Fase 3. Telegram ingestion
Stato: VALIDATA

Validato:
- filtro chat
- raw ingestion
- deduplica minima
- source mapping

---

## Fase 4. Parser minimo operativo
Stato: IMPLEMENTATA (in validazione estesa)

Completato:
- eligibility pre-check
- trader resolution minima
- classificazione minima
- estrazione campi base
- persistenza parse result
- normalizzazione parse result (`ParseResultNormalized`)
- validator minimo non bloccante
- script replay con esempi output normalizzato

In validazione:
- copertura casi reali per update subtype
- qualita `root_ref` e linkage debole

---

## Fase 5. Matching update -> trade
Stato: IN ANALISI

Focus previsto:
- linking robusto update->segnale/trade
- riduzione ambiguita
- regole forti per auto-apply

---

## Priorita attuale
1. Consolidare validazione Fase 4 su dataset piu ampi
2. Ridurre casi `UPDATE` generici con subtype piu precisi
3. Entrare in Fase 5 con base parser stabile
