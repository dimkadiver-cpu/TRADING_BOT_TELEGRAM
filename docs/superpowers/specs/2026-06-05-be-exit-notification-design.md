# BE Exit Notification Design

Date: 2026-06-05

## Goal

Rendere universale la semantica `BE_EXIT` nel control-plane notifiche senza modificare classifier exchange, event ingest o lifecycle event types.

Per ora la definizione di `BE_EXIT` e':

- uscita avvenuta tramite stop;
- la chain risultava `PROTECTED` al momento della proiezione notifica;
- non importa che il PnL netto finale sia leggermente negativo per fee o slippage.

## Problem

Oggi una chiusura via stop su chain protetta arriva come evento exchange/lifecycle `SL_FILLED`, ma il layer notifiche la proietta sempre come `STOP_LOSS`.

Esiste gia' una promozione speciale per `CLOSE_FULL_FILLED` su chain `PROTECTED` -> `BE_EXIT`, ma non esiste la stessa semantica per `SL_FILLED`, che e' il caso normale quando si esegue uno stop protettivo spostato a BE.

Risultato: il runtime ha fatto la cosa giusta, ma la notifica finale racconta la semantica sbagliata.

## Chosen Approach

Approccio scelto: regola unica nel `outbox_writer`, con payload strutturato.

Principi:

- il fatto tecnico exchange resta invariato: `SL_FILLED` continua a significare "stop eseguito";
- la semantica user-facing viene decisa nella proiezione CLEAN_LOG;
- il formatter non deve inferire business logic da solo, deve leggere il payload.

## Design

### 1. Projection Rule

Nel `outbox_writer`, quando viene proiettato `SL_FILLED`:

- se `be_protection_status != "PROTECTED"`:
  - `close_reason = "STOP_LOSS"`
- se `be_protection_status == "PROTECTED"`:
  - `close_reason = "BREAKEVEN_AFTER_TP"`

Il payload di chiusura deve quindi portare un `close_reason` canonico anche per il ramo `SL_FILLED`.

### 2. Formatter Rule

Nel formatter CLEAN_LOG:

- se il payload di un terminal stop ha `close_reason = "STOP_LOSS"`:
  - label e testo restano `POSITION CLOSED` / `Close reason: STOP_LOSS`
- se il payload ha `close_reason = "BREAKEVEN_AFTER_TP"`:
  - label e testo diventano `BE EXIT` / `Close reason: BREAKEVEN_AFTER_TP`

Il formatter deve usare il `close_reason` come sorgente di verita' per il rendering user-facing.

### 3. Scope Boundary

Fuori scope per questo intervento:

- cambiare classifier exchange;
- introdurre un nuovo lifecycle event type;
- ridefinire `BE_EXIT` in funzione del PnL netto;
- normalizzare retroattivamente notifiche gia' inviate;
- aggiungere logica fee-aware per distinguere vero break-even economico da uscita protetta.

## Affected Layers

- `src/runtime_v2/control_plane/outbox_writer.py`
- `src/runtime_v2/control_plane/formatters/clean_log.py`
- test del control-plane per proiezione e formatting

## Acceptance Criteria

1. Una chain non protetta che chiude via `SL_FILLED` continua a produrre una notifica `STOP_LOSS`.
2. Una chain `PROTECTED` che chiude via `SL_FILLED` produce payload con `close_reason = "BREAKEVEN_AFTER_TP"`.
3. Lo stesso caso viene renderizzato come `BE EXIT` e non come `POSITION CLOSED` con `STOP_LOSS`.
4. Nessun cambio richiesto a classifier, event ingest, event processor o schema DB.

## Validation Plan

- test unitario/proiezione per `SL_FILLED` non protetto;
- test unitario/proiezione per `SL_FILLED` protetto;
- test formatter per payload con `close_reason = "BREAKEVEN_AFTER_TP"`;
- verifica che il caso esistente `CLOSE_FULL_FILLED` su chain protetta non regredisca.

## Risks

- la semantica `BE_EXIT` resta nel control-plane notifiche e non nel lifecycle core;
- la regola dipende dal valore persistito di `be_protection_status` letto al momento della proiezione;
- se in futuro servira' distinguere tra "uscita protetta" e "break-even economico netto", andra' introdotto un vocabolario separato.

## Recommendation

Implementare questa soluzione come fix minimo coerente.

Se in seguito servira' una semantica piu' forte e cross-layer, il passo successivo naturale sara' portare il concetto di `protected exit` nel lifecycle invece che solo nella proiezione notifiche.
