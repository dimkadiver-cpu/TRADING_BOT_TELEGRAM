# Hardening When Bot Is Down

## Obiettivo

Ridurre il rischio operativo nei casi in cui:

- il bot si spegne;
- il processore lifecycle si blocca;
- il worker execution non gira;
- il sync verso exchange e' temporaneamente assente.

Il principio guida e':

- tutto cio' che puo' vivere in sicurezza su exchange deve vivere su exchange;
- tutto cio' che richiede coordinamento dinamico del bot deve essere riconosciuto come meno resiliente.

---

## Livelli di protezione

## Livello 1 - protezione exchange-native

E' il livello piu' robusto.

Qui i protettivi sono custoditi direttamente da Bybit:

- stop loss nativo;
- take profit nativo;
- trailing stop nativo;
- TP/SL di posizione (`entire position`) quando applicabile.

### Vantaggio

Se il bot si spegne dopo che l'ordine e' stato accettato e la posizione e' aperta:

- la protezione resta viva su exchange;
- non serve il bot per mantenere la protezione minima.

### Casi migliori

- una sola entry;
- un solo TP;
- uno SL;
- mode C / entry con attached protection oppure TP/SL position-level semplice.

---

## Livello 2 - protezione exchange + bot coordination

Qui la protezione esiste, ma per restare coerente dipende da logica applicativa:

- multi-entry;
- multi-TP partial;
- resize di SL/TP dopo fill successivi;
- cancel di leg residue;
- passaggi a breakeven;
- trailing gestito dal bot;
- update che convertono `LIMIT -> MARKET`;
- update che spostano o ricostruiscono i target.

### Rischio

Se il bot si ferma:

- i protettivi possono continuare a esistere;
- ma possono non restare allineati alla posizione reale.

Esempio classico:

- entra una prima leg da `0.7`;
- vengono creati TP/SL su qty `0.7`;
- entra poi una seconda leg da `0.3`;
- la posizione totale diventa `1.0`;
- se il bot non fa sync, TP/SL possono restare ancora dimensionati su `0.7`.

In questo scenario:

- non sei "senza protezione" in assoluto;
- ma sei protetto in modo incompleto o incoerente.

---

## Cosa supporta davvero Bybit

## 1. Entire Position TP/SL

E' il comportamento piu' vicino a una protezione auto-scalata.

Quando il TP/SL e' impostato a livello posizione intera:

- Bybit gestisce il TP/SL sulla posizione;
- il comportamento e' piu' robusto in assenza del bot rispetto ai partial TP/SL a qty esplicita.

Questo e' il modello preferibile quando la priorita' e':

- massima protezione a bot spento.

## 2. Partial Position TP/SL

Qui Bybit ragiona in `qty`, non in percentuale dinamica della posizione.

Nel runtime attuale questo si riflette nei campi:

- `tpSize`
- `slSize`

Quindi:

- il bot pensa in `%`;
- Bybit riceve e mantiene size esplicite.

Se la posizione cambia dopo:

- Bybit non ricalcola automaticamente i partial TP/SL nel modo desiderato dal bot;
- serve sync o rebuild applicativo.

## 3. Trailing Stop nativo

Se disponibile e coerente con la strategia:

- aumenta molto la resilienza;
- riduce la dipendenza dal bot per lo spostamento dello stop.

---

## Strategia consigliata per aumentare la resilienza

## Profilo A - massima robustezza

Usare preferibilmente:

- una sola entry;
- TP/SL nativi;
- trailing stop nativo opzionale;
- nessun partial TP complesso;
- nessun resize dipendente dal bot.

### Risultato

Se il bot muore dopo l'apertura:

- la posizione resta comunque protetta a livello base.

## Profilo B - compromesso

Usare:

- una o piu' entry;
- un solo TP position-level;
- SL nativo;
- logica bot solo per update non critici.

### Risultato

Protezione buona, ma alcuni aggiustamenti avanzati si perdono se il bot e' giu'.

## Profilo C - massima flessibilita', meno robustezza offline

Usare:

- multi-entry;
- multi-TP partial;
- resize dinamici;
- breakeven / trailing / sync applicativi.

### Risultato

Massima espressivita' strategica, ma:

- forte dipendenza dal bot;
- forte bisogno di reconciliation corretta;
- rischio piu' alto di incoerenza se il bot si ferma.

---

## Regole architetturali consigliate

## 1. Exchange-first quando possibile

Se una protezione puo' essere nativa exchange:

- preferirla a una protezione bot-managed.

## 2. Non assumere auto-resize dei partial TP/SL

Per i partial TP/SL su Bybit:

- trattare il bot come owner della coerenza size;
- non assumere che l'exchange aggiorni da solo i volumi nel modo voluto.

## 3. Riconoscere i casi "unsafe when bot down"

Il runtime dovrebbe marcare come meno resilienti i casi:

- `deferred_market + multi_tp_partial`
- `MARKET + LIMIT` con TP partial;
- update `LIMIT -> MARKET`;
- resize dopo fill successivi;
- qualsiasi scenario in cui la size reale cambia dopo il primo set di protettivi.

## 4. Modalita' conservative

Dove non esiste sync affidabile, meglio:

- bloccare;
- mandare in review;
- oppure ridurre la strategia a una forma piu' robusta.

---

## Contromisure operative raccomandate

## 1. Watchdog di protezione

Processo separato che controlla periodicamente:

- posizione aperta esiste;
- SL esiste davvero su exchange;
- TP attesi esistono davvero su exchange;
- size protettive sono coerenti con `open_position_qty`.

Se trova mismatch:

- alza alert;
- opzionalmente tenta repair.

## 2. Reconciliation conservativa

Mai trattare un dato parziale exchange come verita' assoluta.

Se il sync e' incompleto o ambiguo:

- non cancellare protettivi automaticamente;
- non assumere che una posizione sia chiusa solo perche' non la vedi in una risposta incompleta;
- preferire retry / incomplete-state / review.

## 3. Alerting esplicito

Segnali da monitorare:

- posizione aperta senza SL su exchange;
- qty TP/SL inferiore a `open_position_qty` quando non previsto;
- comandi `SYNC_PROTECTIVE_ORDERS` falliti o ripetuti;
- bot down con posizioni aperte non exchange-native.

## 4. Policy differenziata per modalita'

Quando il sistema rileva modalita' ad alto rischio operativo:

- consentire solo setup semplici;
- oppure richiedere conferma/review manuale.

---

## Matrice sintetica

| Scenario | Bot spento | Robustezza |
|---|---|---|
| 1 entry + 1 TP + 1 SL native | protetto | alta |
| 1 entry + trailing stop native | protetto | alta |
| 1 entry + TP/SL position-level | protetto | medio-alta |
| multi-entry + 1 TP semplice | parzialmente protetto | media |
| multi-entry + multi-TP partial | protezione non garantita coerente | bassa |
| LIMIT -> MARKET via update con resize protettivi | dipendente dal bot | bassa |

---

## Implicazione per il runtime di questo repository

Per aumentare la sicurezza reale quando il bot non funziona:

1. preferire mode semplici exchange-native dove possibile;
2. introdurre chiaramente il concetto di:
   - `exchange-safe`
   - `bot-dependent`
3. evitare di considerare equivalenti i due mondi;
4. usare `review_required` o downgrade della strategia nei casi in cui il resize protettivo non e' sicuro.

---

## Regola finale

Se la domanda e':

> "Anche se il bot muore, la posizione resta protetta?"

la risposta corretta e':

- **si'**, se la protezione principale vive gia' su Bybit in forma nativa e non richiede resize dinamici;
- **non abbastanza**, se la protezione dipende da partial TP/SL, sync size o update successivi gestiti dal bot.



Metodi utili

  1. TP/SL nativo su exchange

  - È la protezione principale.
  - Su Bybit puoi usare Set Trading Stop per TP/SL di posizione.
  - La modalità più robusta è Entire Position: è la più vicina a una protezione che si adatta alla size aperta senza
    dipendere dal bot.
    Docs:
  - API Set Trading Stop: https://bybit-exchange.github.io/docs/v5/position/trading-stop
  - Help Center TP/SL: https://www.bybit.com/en/help-center/article/Introduction-to-Enhanced-Take-Profit-Stop-Loss-TP-SL

  2. Trailing Stop nativo

  - Anche questo vive su exchange.
  - Utile se vuoi che la protezione segua il prezzo senza bot acceso.
    Help:
  - https://www.bybit.com/en/help-center/article/?id=000001140&language=en_US

  3. Ridurre i casi che richiedono sync

  - Se usi 1 entry + 1 TP + 1 SL in mode C / entire-position logic, la resilienza è molto più alta.
  - Se usi multi-entry + multi-TP partial, la protezione diventa molto più dipendente dal bot.

  4. Ordini di uscita reduce-only / close-on-trigger

  - Sono utili come meccanismi difensivi aggiuntivi.
  - Non sostituiscono il TP/SL position-level, ma aiutano a evitare ordini che aumentano la posizione per errore.
    Riferimento:
  - https://www.bybit.com/en-US/help-center/s/article/What-is-a-limit-market-conditional-order/

  5. Watchdog / secondo processo

  - Non è protezione exchange-native, ma aumenta robustezza operativa:
      - processo secondario che controlla che per ogni posizione aperta esista davvero lo SL su exchange;
      - se manca, allarme immediato o ripristino.
  - Questo serve soprattutto nei casi partial TP/SL.

  Limite strutturale

  - Se vuoi multi-TP partial, Bybit lavora con qty esplicite (tpSize, slSize), non con percentuali dinamiche della
    posizione.
  - Quindi lì la “protezione magica” completa non c’è: serve comunque il bot per riallineare le size dopo nuovi fill o
    cancellazioni.

  Conclusione pratica
  Se il tuo obiettivo è “anche se il bot muore, resto protetto”:

  - migliore opzione: entry semplice + SL/TP nativi di posizione + trailing stop opzionale
  - peggiore opzione per resilienza: multi-entry + multi-TP partial con resize dinamici

  Se vuoi, posso aggiungere anche questa sezione al documento come “Hardening when bot is down”.