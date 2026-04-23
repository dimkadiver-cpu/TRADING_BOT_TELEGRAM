# Fix - Marker FreqUI piu leggibili e plot coerente col bridge

Questo documento contiene un mini piano operativo e due prompt pronti da usare.

Obiettivo:

- rendere piu leggibili i marker nel chart FreqUI
- ridurre il mismatch tra marker del grafico e trade reali gestiti dal bridge

Riferimenti:

- `freqtrade/user_data/strategies/SignalBridgeStrategy.py`
- `src/execution/freqtrade_callback.py`
- `src/execution/freqtrade_normalizer.py`
- `src/execution/exchange_order_manager.py`
- `docs/FREQTRADE_CONFIG.md`
- `docs/FREQTRADE_RUNBOOK.md`

## Mini piano

1. Verificare come Freqtrade/FreqUI disegna oggi i marker della strategy e quali hook sono disponibili per migliorare la leggibilita.
2. Introdurre un `plot_config` piu esplicito nella strategy per rendere visibili contesto, livelli e stato del bridge.
3. Identificare una fonte affidabile per plottare eventi reali del bridge, senza dipendere solo da `enter_long` e `exit_long`.
4. Aggiungere una patch minima e conservativa che renda il grafico piu coerente con:
   - entry fill reali
   - partial exit reali
   - stop/TP rilevanti
5. Testare che il fix non rompa il comportamento attuale della strategy.
6. Aggiornare la documentazione minima operativa se cambia il modo corretto di leggere il chart.

## Prompt 1 - Marker piu leggibili in FreqUI

```text
Leggi:
- freqtrade/user_data/strategies/SignalBridgeStrategy.py
- docs/FREQTRADE_CONFIG.md
- docs/FREQTRADE_RUNBOOK.md

Obiettivo: rendere il chart FreqUI piu leggibile senza cambiare ancora la semantica del bridge.

Fai queste cose in ordine:

1. Verifica quali elementi del dataframe e del `plot_config` di Freqtrade possono essere usati in modo affidabile.
2. Aggiungi alla strategy un `plot_config` minimo ma utile per mostrare:
   - stoploss di riferimento
   - take profit rilevanti
   - eventuali marker/serie utili a leggere il contesto del trade
3. Se servono colonne aggiuntive nel dataframe per plottare meglio il contesto, aggiungile in modo conservativo.
4. Non introdurre logiche di trading nuove: il fix deve essere solo di osservabilita del chart.
5. Aggiorna o aggiungi test mirati se la strategy espone nuovi elementi plottabili.
6. Aggiorna la documentazione minima per spiegare come leggere il chart dopo il fix.

Verifica finale:
- il chart espone marker o linee piu leggibili
- nessuna regressione del comportamento runtime
- i test rilevanti passano
```

## Prompt 2 - Plot entry/exit reali coerenti col bridge

```text
Leggi:
- freqtrade/user_data/strategies/SignalBridgeStrategy.py
- src/execution/freqtrade_callback.py
- src/execution/freqtrade_normalizer.py
- src/execution/exchange_order_manager.py
- docs/FIX_FREQUI_MARKERS.md

Prerequisito: Prompt 1 completato e testato.

Obiettivo: rendere il chart piu coerente con i trade reali del bridge, non solo con i trigger `enter_long` / `exit_long`.

Fai queste cose in ordine:

1. Analizza quali eventi reali del bridge sono disponibili e affidabili per il plotting:
   - entry fill
   - partial exit
   - close full
   - TP fill
   - stop fill
2. Scegli una soluzione minima e conservativa per riflettere questi eventi nel chart.
3. Implementa la patch evitando doppie fonti di verita o marker ingannevoli.
4. Se necessario, aggiungi una normalizzazione read-only dal DB per esporre eventi reali al layer di plotting.
5. Mantieni separata la logica di esecuzione dalla logica di visualizzazione.
6. Scrivi test per verificare che il plotting rifletta meglio:
   - entry reali fillate
   - partial exit reali
   - backward compatibility del comportamento attuale
7. Aggiorna la documentazione con il significato dei marker visualizzati.

Verifica finale:
- il chart non induce piu a pensare che ci siano zero entry/exit quando i fill reali esistono
- la soluzione resta conservativa e auditabile
- i test esistenti e i nuovi test passano
```

## Regole

```text
- Non cambiare la logica di trading solo per far tornare il grafico.
- Non confondere marker di segnale con marker di fill reale.
- Se il chart continua ad avere limiti strutturali di FreqUI, documentali esplicitamente.
- Ogni miglioramento visuale deve restare compatibile con `strategy_managed` e `exchange_manager`.
```

## Stato implementazione

Entrambi i prompt sono stati completati e testati.

### Come leggere il chart FreqUI dopo il fix

Il chart ora mostra due tipi di informazioni distinti:

#### 1. Marker nativi di Freqtrade (triangoli entry/exit)

Questi sono i marker standard `enter_long` / `exit_long` gestiti dal dataframe della strategy.
Rappresentano il **momento in cui la strategy ha emesso un segnale**, non necessariamente un fill reale.

- Possono mostrare `Long entries: 0` anche quando un trade è stato fillato, perché il contatore dipende dalle righe del dataframe, non dagli ordini reali.
- Questo è un **limite strutturale di FreqUI** — non è un bug del bridge.

#### 2. Linee di contesto bridge (main plot)

Linee orizzontali aggiunte da `populate_indicators` leggendo il contesto del trade attivo:

| Colonna              | Colore   | Significato                                    |
|----------------------|----------|------------------------------------------------|
| `bridge_sl`          | Rosso    | Stoploss di riferimento dal segnale            |
| `bridge_tp1`         | Verde    | Take profit livello 1                          |
| `bridge_tp2`         | Verde chiaro | Take profit livello 2                      |
| `bridge_tp3`         | Verde pallido | Take profit livello 3                     |
| `bridge_entry_price` | Blu      | Prezzo entry dal piano del segnale             |

Queste linee sono presenti solo quando esiste un trade ACTIVE per la pair. Scompaiono quando il trade è chiuso.

#### 3. Subplot "Bridge Events" (eventi reali dal DB)

Barre nel subplot "Bridge Events" che rappresentano **fill reali** registrati nel DB:

| Colonna                     | Colore    | Significato                                |
|-----------------------------|-----------|-------------------------------------------|
| `bridge_event_entry`        | Blu       | Entry fillata (ordine eseguito)           |
| `bridge_event_partial_exit` | Arancione | Partial exit fillata                      |
| `bridge_event_tp_hit`       | Verde     | Take profit colpito                       |
| `bridge_event_sl_hit`       | Rosso     | Stoploss colpito                          |
| `bridge_event_close`        | Viola     | Posizione chiusa completamente            |

Questi eventi vengono letti dalla tabella `events` del DB e mappati sulla candela più vicina al timestamp dell'evento.

#### Differenza chiave

- **Marker nativi** = segnale emesso dalla strategy → può non corrispondere a un fill
- **Bridge Events subplot** = fill reale dal DB → fonte di verità per l'esecuzione
- **Linee contesto** = livelli di riferimento dal segnale attivo → SL/TP/entry

Se il chart mostra `Long entries: 0` ma il subplot Bridge Events mostra una barra `bridge_event_entry`, significa che l'entry è stata realmente eseguita ma il marker nativo non è stato aggiornato. Questo è il comportamento atteso.

### Limiti strutturali di FreqUI

1. I contatori `Long entries` / `Long exit` nel pannello contano solo le righe del dataframe con `enter_long=1` / `exit_long=1`. Il bridge imposta questi valori solo sulla candela corrente per triggerare l'ordine, poi vengono resettati. FreqUI non ha modo di contare fill reali.
2. I marker a triangolo standard non possono essere personalizzati nel colore o nella forma dal `plot_config`.
3. Il subplot "Bridge Events" è un workaround: FreqUI non supporta overlay di eventi arbitrari direttamente sul main plot.
