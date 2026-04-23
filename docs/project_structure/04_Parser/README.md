# 04 Parser - Indice

Questa cartella documenta il parser attuale del progetto, con focus su:

- percorso runtime reale dal Router al parser;
- doppio output (`parse_results` legacy + `parse_results_v1` Canonical v1);
- responsabilita dei moduli principali;
- guida operativa per estendere i profili trader senza regressioni.

## Ordine di lettura consigliato

1. `01_architettura_e_flusso.md`
2. `02_componenti_core.md`
3. `03_contratti_output.md`
4. `04_integrazione_router_storage.md`
5. `05_test_e_debug.md`
6. `06_estendere_parser.md`
7. `07_mappa_file_parser.md`

## Scope di questa documentazione

- In scope: `src/parser/*`, integrazione in `src/telegram/router.py`, validazione in `src/validation/coherence.py`, persistenza parser in `src/storage/parse_results*.py`.
- Out of scope: dettaglio execution/freqtrade e operation rules interne (coperte in documentazione layer dedicata).
