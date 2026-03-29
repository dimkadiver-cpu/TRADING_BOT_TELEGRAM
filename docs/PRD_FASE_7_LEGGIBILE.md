# Fase 7 — Backtesting: cosa vuoi costruire (versione leggibile)

---

## Il problema che risolve:

1) Hai un bot che riceve segnali di trading da trader su Telegram, li interpreta, e li esegue in automatico.
Funziona. Ma non sai se quei segnali, storicamente, avrebbero fatto guadagnare o perdere.
2) Verifica rapida di una fonte di segnale, se i segnali sono validi e eventilamente in quale schenario si aha un output migliore. 


Fase 7 risponde a tre domande:

0. **La fonte ha segnali validi** — I segnali sono statisticamnete validi?
1. **Il parser funziona bene?** — I segnali che il bot ha capito erano sensati finanziariamente?
2. **La configurazione è ottimale?** — Se avessi usato 2% di rischio invece di 1%, saresti andato meglio?
3. **Quanto avresti guadagnato/perso?** — Win rate, drawdown, profitto totale per ogni trader.

---

## Come funziona (in parole semplici)

Immagina di avere un registro di tutti i messaggi ricevuti dai trader negli ultimi mesi.
Fase 7 prende quel registro e "riproduce" le operazioni di trading su dati di mercato reali,
come se il bot le avesse eseguite davvero in quel momento.

Il processo è questo:

```
1. Prendo tutti i segnali storici dal database
   (quelli già parsati e processati dal bot)

2. Li raggruppo in "storie complete":
   segnale originale + tutti gli aggiornamenti successivi
   (es: "entra su BTC a 90.000" → "TP1 colpito" → "sposta SL a breakeven" → "chiudi tutto")

3. Testo ogni storia su diversi scenari
   (vedi sotto cosa significa)

4. Lancio freqtrade in modalità backtest
   (freqtrade simula le esecuzioni sui candlestick reali di Bybit)

5. Produco un report con i risultati
   (tabella comparativa tra scenari, grafici, metriche)
```

---

## Cos'è una "storia completa" (SignalChain)

Ogni segnale non è solo un messaggio. È una sequenza:

```
[14:02] Trader: "LONG BTC, entry 90.000, SL 88.000, TP1 92.000 TP2 95.000"
[14:45] Trader: "TP1 colpito ✓"
[14:46] Trader: "Sposto SL a breakeven (90.000)"
[17:30] Trader: "Chiudo tutto"
```

Il sistema ricostruisce questa sequenza e la usa per simulare l'operazione nel modo più fedele possibile.

---

## Gli scenari di test

Non testi una sola strategia di gestione. Ne testi sei diverse in parallelo,
partendo dagli stessi segnali storici:

| Scenario | Cosa testa |
|----------|-----------|
| **follow_full_chain** | Segui tutto quello che il trader ha detto (move SL, chiusure parziali, ecc.) |
| **signals_only** | Ignora tutto tranne l'apertura. Chiudi solo quando SL o TP originali vengono colpiti. |
| **sl_to_be_after_tp2** | Come follow_full_chain, ma aggiungi automaticamente: quando TP2 viene colpito, sposta SL a breakeven. |
| **aggressive_averaging** | Come follow_full_chain, ma distribuisci il 50% del capitale su ogni entry averaging. |
| **double_risk** | Come follow_full_chain, ma usa 2% di rischio per trade invece del default 1%. Il sizing viene ricalcolato. |
| **gate_warn** | Come follow_full_chain, ma includi anche i segnali che il sistema aveva bloccato (per vedere cosa avresti perso/guadagnato). |

Al termine hai una tabella così:

| scenario | trades | win rate | profitto % | max drawdown | profit factor |
|----------|--------|----------|------------|--------------|---------------|
| follow_full_chain | 45 | 62% | +18.4% | -8.1% | 1.82 |
| signals_only | 45 | 56% | +11.2% | -12.3% | 1.41 |
| sl_to_be_after_tp2 | 45 | 64% | +19.1% | -6.2% | 2.01 |
| ... | | | | | |

---

## Realismo: come evitiamo di "barrare"

Un backtest può essere ottimistico se non è attento ai dettagli. Queste sono le scelte fatte per mantenere il realismo:

- **Prezzo di entrata realistico**: la trade apre al prezzo che il trader aveva indicato (limit order), non al prezzo di apertura della candela successiva.
- **Aggiornamenti ritardati di 1 candela (5 minuti)**: se il trader ha scritto "sposta SL" alle 14:46, nel backtest quella modifica viene applicata dalla candela delle 14:50 in poi, non immediatamente.
- **Chiusure parziali reali**: se il trader ha chiuso il 30% al TP1 e il 30% al TP2, il backtest lo riproduce con le stesse percentuali.
- **Entries multiple (averaging)**: se il segnale aveva 3 prezzi di entrata, il backtest:
  - per `follow_full_chain`: usa la storia reale (se il trader ha cancellato E2 prima che venisse colpita, E2 non viene simulata)
  - per gli altri scenari: simula E2/E3 se il prezzo di mercato ci arriva davvero

---

## Come gestisce il sizing (dimensione posizione)

Per la maggior parte degli scenari, usa il sizing che il sistema aveva calcolato al momento reale del segnale (es. 45 USDT). Questo risponde alla domanda "cosa sarebbe successo davvero?".

Per lo scenario `double_risk` (2% invece di 1%), il sizing viene ricalcolato (es. 90 USDT). Questo risponde alla domanda "e se avessi rischiato di più?".

---

## I dati di mercato (candlestick)

Prima di lanciare il backtest, scarichi i dati storici di Bybit tramite freqtrade:

```
freqtrade download-data --pairs BTC/USDT:USDT ETH/USDT:USDT ... --timeframe 5m --days 365
```

Questi dati vengono usati da freqtrade per simulare i movimenti di mercato minuto per minuto.

---

## Cosa produce alla fine

Per ogni run di backtest (= tutti gli scenari su tutti i trader in un dato periodo):

```
backtest_reports/run_2026-03-28/
  comparison_table.csv      ← tabella comparativa scenari (apri con Excel) // piu altre statistiche mensili: numero trade, winrate %, pln ecc
  comparison_table.html     ← stessa tabella, navigabile nel browser
  summary.json              ← dati machine-readable

  per_scenario/
    follow_full_chain/
      trades.csv            ← lista di tutte le trade con P&L
      equity_curve.csv      ← curva del capitale nel tempo
      freqtrade_charts/     ← grafici interattivi con entry/exit su candlestick

  parser_quality/
    signal_coverage.csv     ← quante chain erano complete (utile per validare il parser)
    update_chain_stats.csv  ← quanti UPDATE per segnale, quali intent più frequenti
```

---

## Cose che questo sistema NON fa

- Non tocca i dati di trading live (tutti i dati storici sono read-only)
- Non esegue ordini reali — è solo simulazione
- Non re-parsa i messaggi — usa i risultati del parser già salvati nel database

---

## In breve: cosa ottieni

Dopo Fase 7 puoi rispondere a domande come:
- "Trader 3 ha un win rate del 62% seguendo tutta la sua chain, ma solo 56% ignorando gli aggiornamenti. Vale la pena seguirlo?"
- "Spostare lo SL a breakeven dopo TP2 riduce il drawdown da -8% a -6% mantenendo lo stesso profitto. Ha senso farlo in automatico?"
- "Con il 2% di rischio il profitto raddoppia ma il drawdown sale a -15%. Il rischio vale?"
- "Il 15% dei segnali che il gate aveva bloccato erano in realtà profittevoli. I criteri di blocco sono troppo restrittivi?"


## Note importanti:
   1. Attualmente la fase 7 è integrata nel Applicazione generale. sucessivamente dovra essere autonoma
   2. Deve usare i moduli attualamente condivi e propri

   Logica generale: 
   - Scarico i dati raw dalla fonte
   - applico il parser gia essistenete o fatto specificamente 
   - applico il processo di simulazione