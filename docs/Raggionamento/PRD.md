# PRD - Bybit Main Demo Execution Support

**Data:** 2026-05-17  
**Stato:** draft approvato per pianificazione  
**Ambito:** Runtime V2 Execution Gateway, adapter diretto Bybit Demo, Hummingbot custom parallelo

## 1. Contesto

Runtime V2 oggi passa dall'Execution Gateway e usa un adapter Hummingbot API orientato a perpetual/futures. Il primo test end-to-end richiede un ambiente exchange compatibile con:

- posizioni LONG/SHORT;
- leva;
- ordini reduce-only;
- lettura posizione;
- close parziale o totale;
- stop loss e take profit collegati alla posizione.

L'utente non puo' creare API Bybit perpetual testnet per limite regionale. Le chiavi disponibili possono essere solo Bybit Main Demo oppure, in alternativa, spot. Spot non e' sufficiente per il flusso attuale perche' non espone la stessa semantica di posizione perpetual.

Bybit Main Demo usa `https://api-demo.bybit.com`, ma l'immagine Hummingbot attuale non espone un connector demo dedicato. Il codice Hummingbot verificato nel container conosce `https://api.bybit.com/` e `https://api-testnet.bybit.com/`, non `api-demo.bybit.com`.

## 2. Obiettivo

Abilitare Runtime V2 a eseguire un primo test end-to-end su Bybit Main Demo senza dipendere dalla creazione di API perpetual testnet.

Il risultato finale deve supportare due binari:

1. adapter diretto Runtime V2 -> Bybit Main Demo API;
2. Hummingbot separato/parallelo con connector custom verso Bybit Main Demo.

Il primo binario sblocca il test prima possibile. Il secondo conserva una strada compatibile con l'architettura Hummingbot esistente.

## 3. Non Obiettivi

- Non implementare supporto spot nel MVP.
- Non usare API key reali nei file versionati.
- Non modificare il Runtime V2 lifecycle per adattarlo a spot.
- Non sostituire l'Execution Gateway.
- Non abilitare live trading mainnet.
- Non rimuovere lo stack Hummingbot attuale.

## 4. Architettura Target

### Binario A - Adapter Diretto Bybit Demo

```text
Runtime V2
  -> Execution Gateway
  -> BybitDemoAdapter
  -> https://api-demo.bybit.com
```

Questo e' il percorso MVP. L'adapter diretto implementa solo le funzioni necessarie per il primo test del lifecycle Runtime V2.

### Binario B - Hummingbot Custom Parallelo

```text
Runtime V2
  -> Execution Gateway
  -> Hummingbot API demo separata
  -> Hummingbot custom demo
  -> https://api-demo.bybit.com
```

Questo percorso usa un secondo stack Docker o servizi separati, con porte e volumi indipendenti dallo stack Hummingbot esistente.

## 5. Configurazione Attesa

### `.env`

Le chiavi demo devono stare solo in `.env`:

```env
BYBIT_DEMO_API_KEY=...
BYBIT_DEMO_API_SECRET=...
```

Per Hummingbot custom parallelo possono servire anche variabili dedicate:

```env
HUMMINGBOT_DEMO_BASE_URL=http://localhost:8001
HUMMINGBOT_DEMO_SECRET=...
```

Le chiavi non devono essere stampate nei log, nei test, nei report o nella documentazione.

### `config/execution.yaml`

Esempio di routing per adapter diretto:

```yaml
execution:
  default_adapter: bybit_v5_demo

  account_routing:
    default:
      adapter: bybit_v5_demo
      execution_account_id: bybit_demo_main

  adapters:
    bybit_v5_demo:
      type: bybit_v5
      mode: demo
      base_url: https://api-demo.bybit.com
      category: linear
      leverage: 1
      api_key_env: BYBIT_DEMO_API_KEY
      api_secret_env: BYBIT_DEMO_API_SECRET

      entry_execution:
        mode: b_entry_stop_then_tp

      capabilities:
        place_entry: true
        protective_stop_native: false
        take_profit_native: false
        bracket_order: false
        move_stop: false
        close_partial: true
        close_full: true
        executor_position: false

      live_safety:
        allow_live_trading: false
```

Esempio futuro per Hummingbot custom parallelo:

```yaml
execution:
  adapters:
    hummingbot_api_demo:
      type: hummingbot_api
      mode: demo
      base_url: http://localhost:8001
      connector: bybit_perpetual_demo
      leverage: 1
      live_safety:
        allow_live_trading: false
```

## 6. Requisiti Funzionali

### Adapter Diretto Bybit Demo

L'adapter diretto deve supportare:

- firma HMAC Bybit V5;
- lettura clock/timestamp compatibile con Bybit;
- `set leverage`;
- `place order` per entry market e limit;
- `cancel order`;
- lettura stato ordine;
- lettura posizione;
- close full;
- close partial;
- mapping degli errori Bybit in stati Runtime V2 coerenti.

TP/SL nativi sono fase successiva se la verifica su Bybit Demo conferma endpoint e comportamento. Nel primo MVP possono restare non supportati e produrre `REVIEW_REQUIRED` se richiesti oltre la capacita' dichiarata.

### Hummingbot Custom Parallelo

Lo stack custom deve:

- usare un'immagine Hummingbot separata;
- aggiungere un domain o connector `bybit_perpetual_demo`;
- puntare REST a `https://api-demo.bybit.com`;
- verificare eventuali WebSocket demo;
- esporre Hummingbot API su porta diversa, ad esempio `8001`;
- usare volumi separati per config, log e dati;
- non interferire con i container Hummingbot esistenti.

## 7. Requisiti Non Funzionali

- Sicurezza: nessun secret in repository, log o output test.
- Isolamento: Hummingbot demo separato dallo stack attuale.
- Idempotenza: `client_order_id` Runtime V2 resta deterministico.
- Osservabilita': errori adapter devono essere leggibili in `result_payload_json` senza includere secret.
- Reversibilita': si deve poter tornare a `hummingbot_api_paper` cambiando config.
- Testabilita': chiamate HTTP Bybit devono essere mockabili.

## 8. Sequenza di Implementazione

### Fase 1 - Adapter Diretto Minimo

1. Aggiungere modello config per `bybit_v5_demo`.
2. Implementare firma e client HTTP Bybit V5.
3. Implementare `place entry`, `cancel`, `get order status`, `get positions`.
4. Implementare `close_full` e `close_partial` con reduce-only.
5. Aggiungere test unitari con HTTP mockato.
6. Aggiungere test gated manuale con chiavi demo.

### Fase 2 - Validazione Runtime V2 End-to-End

1. Configurare `.env` con chiavi demo.
2. Configurare `config/execution.yaml` su `bybit_v5_demo`.
3. Avviare Runtime V2.
4. Eseguire un segnale controllato con size minima.
5. Verificare creazione command, invio ordine, ACK, posizione e close.

### Fase 3 - TP/SL Demo

1. Verificare endpoint Bybit Demo per stop e take profit su categoria `linear`.
2. Decidere se usare ordini condizionali separati o trading-stop.
3. Aggiornare capabilities.
4. Aggiungere test specifici per TP/SL.

### Fase 4 - Hummingbot Demo Parallelo

1. Creare patch connector Hummingbot per `bybit_perpetual_demo`.
2. Costruire immagine Docker custom.
3. Aggiungere compose separato o profilo demo.
4. Esporre API demo su `localhost:8001`.
5. Collegare Runtime V2 tramite adapter Hummingbot API esistente.
6. Validare parita' comportamento con adapter diretto.

## 9. Acceptance Criteria

- `config/execution.yaml` puo' selezionare `bybit_v5_demo`.
- Le chiavi demo vengono lette da `.env`.
- Un comando `PLACE_ENTRY` Runtime V2 viene tradotto in ordine Bybit Demo valido.
- Il gateway salva `client_order_id`, stato invio e risposta adapter.
- `cancel order` funziona su ordine aperto.
- `close_full` funziona su posizione demo aperta.
- Errori Bybit 4xx diventano fallimenti non retryable.
- Errori timeout/5xx seguono la retry policy Runtime V2.
- Se TP/SL non sono ancora supportati, il comando diventa `REVIEW_REQUIRED` con motivo esplicito.
- Hummingbot demo parallelo usa porta e volumi separati.
- Lo stack Hummingbot attuale continua a funzionare senza modifiche obbligatorie.
- Nessun test o log stampa API key o secret.

## 10. Rischi

| Rischio | Impatto | Mitigazione |
|---|---|---|
| Bybit Demo differisce da mainnet/testnet su endpoint V5 | ordini o posizioni non funzionano | test gated reale con size minima |
| TP/SL demo non compatibili con mapping attuale | lifecycle incompleto | dichiarare capability false finche' non verificato |
| Hummingbot connector richiede piu' patch del previsto | ritardo binario B | consegnare prima adapter diretto |
| Rate limit o clock skew | rifiuti API | recv window configurabile e sync timestamp |
| Secret leakage | rischio operativo | masking centralizzato e test su log |

## 11. Decisione

Si procede con entrambi i binari, in questo ordine:

1. **MVP:** adapter diretto `bybit_v5_demo`.
2. **Secondo binario:** Hummingbot custom parallelo `bybit_perpetual_demo`.

Questa scelta sblocca il test end-to-end rapidamente e mantiene aperta la compatibilita' con Hummingbot senza far dipendere il primo test da una build custom.

## 12. Suggested Commit Message

```text
docs(runtime-v2): add PRD for Bybit Main Demo execution support
```
