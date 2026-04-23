# Parser - Guida Estensione (Nuovo Trader o Refactor)

## 1. Aggiungere un nuovo profilo trader

Passi minimi:

1. creare cartella `src/parser/trader_profiles/<trader_id>/`;
2. aggiungere `parsing_rules.json`;
3. implementare `profile.py` con `parse_message(...)`;
4. registrare factory in `registry.py`;
5. aggiungere test profilo (`real_cases`, `canonical_output`, smoke);
6. verificare integrazione con Router su messaggi reali/replay.

Se possibile, implementare anche `parse_canonical(...)` per bypassare normalizer shadow.

## 2. Regole pratiche per modifiche sicure

- mantenere backward compatibility di `entities` se phase4 dipende dai campi;
- se cambi intent names, aggiornare anche `intent_action_map` e validation;
- se cambi target extraction, verificare `target_scope`/`linking` e resolver;
- se cambi semantica update, testare applicazione runtime update planner/applier.

## 3. Checklist prima del merge

1. parser profile tests verdi sul trader impattato;
2. `test_rules_engine` verde se tocchi regole/classificazione;
3. `tests/parser_canonical_v1/*` verdi se tocchi adapter/normalizer/models;
4. replay parser con report coerente su dataset storico;
5. nessun aumento inatteso di `UNCLASSIFIED` o `normalizer_error`.

## 4. Anti-pattern da evitare

- fix locali nel Router per compensare bug parser profile;
- aggiungere branch non testati in `intent_action_map`;
- cambiare output legacy senza controllare validation/operation rules;
- usare warning come sostituto di un mapping corretto.

## 5. Rischi noti e doc drift

- `src/parser/canonical_schema.py` tenta di leggere `schema_consigliato_finale_parser.csv` nella root repository.
- Nel workspace corrente il file non risulta presente, quindi il caricamento schema canonicale puo ricadere in fallback vuoto.

Implicazione:

- funzioni che dipendono dal CSV (azioni canonicali/support matrix) possono dare risultati ridotti rispetto all'intento progettuale.

Azioni consigliate:

1. verificare se il CSV va ripristinato/ricollocato;
2. in alternativa, spostare source-of-truth in un path documentato e coperto da test.

