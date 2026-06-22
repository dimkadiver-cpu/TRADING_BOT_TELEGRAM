# `/trades` e `/trade #n` — Template e funzionalita`

## Scopo

Questa spec definisce due livelli complementari:

- `/trades` = vista lista sintetica;
- `/trade #n` = vista audit completa del singolo trade.

### Principio

```text
/trades
-> serve a trovare rapidamente il trade giusto.

/trade #n
-> serve a capire tutto quello che e` successo su quel trade.
```

Il modello non e` solo "lista + dettaglio".
`/trade #n` e` la rappresentazione narrativa del ciclo di vita del trade, dalla ricezione del segnale fino a chiusura, cancellazione o blocco.

---

## Regole globali

- stesso scope del topic corrente: account oppure trader
- nel `commands` topic generale e` supportato anche `global scope`
- output sempre testuale
- `/trade #n` resta un singolo messaggio, non una vista navigabile
- quando esiste, ogni evento deve avere il riferimento al `clean_log`
- `Source: Signal` deve essere link al `clean_log` del segnale di origine
- anche gli altri eventi devono linkare il relativo `clean_log` quando disponibile

---

## `/trades`

## Scopo

`/trades` mostra la lista sintetica dei trade nel perimetro corrente.

Non e` una vista audit.
Deve ottimizzare scansione, riconoscimento e salto rapido a `/trade #n`.

### Scope supportati

| Scope | Header | Note template |
|---|---|---|
| Global scope | `All accounts` | ogni item mostra `Trader` e `Account` |
| Account scope | `demo_1` | il trader compare solo se utile |
| Trader scope | `demo_1 · trader_a` | il trader non va ripetuto nell'item |

## Header `/trades`

```text
📊 TRADES — <account> [· <trader>]
- - - - - - - - - - - - - - - - - - - -
Total: <n>   Updated: HH:MM:SS
Filters: <active filters>
- - - - - - - - - - - - - - - - - - - -
```

### Regole header

- `Total` = totale dei trade restituiti dal comando
- `Filters:` compare solo se esistono filtri applicati
- `Updated` e` il tempo della query/render

---

## Template `/trades` — caso base

```text
📊 TRADES — demo_1
- - - - - - - - - - - - - - - - - - - -
Total: 3   Updated: 14:32:05
- - - - - - - - - - - - - - - - - - - -
#5 · BTC/USDT · LONG · OPEN
uPnL: +12.40 USDT  rPnL: +0.00 USDT
Details: /trade #5
- - - - - - - - - - - - - - - - - - - -
#7 · ETH/USDT · SHORT · OPEN
uPnL: -3.20 USDT  rPnL: -0.20 USDT
Details: /trade #7
- - - - - - - - - - - - - - - - - - - -
#9 · SOL/USDT · LONG · WAITING_ENTRY
rPnL: —
Details: /trade #9
```

### Regole `/trades`

- stesso principio di compattezza del dashboard
- target `3 righe` per trade
- niente `entry / tp / sl`
- niente timeline eventi
- niente azioni inline nella lista, salvo futura scelta esplicita
- la CTA primaria e` `Details: /trade #n`

---

## `/trades` con filtro trader

```text
📊 TRADES — demo_1 · trader_a
- - - - - - - - - - - - - - - - - - - -
Total: 1   Updated: 14:32:05
- - - - - - - - - - - - - - - - - - - -
#5 · BTC/USDT · LONG · PARTIALLY_CLOSED
uPnL: +34.20 USDT  rPnL: +14.20 USDT
Details: /trade #5
```

---

## `/trades` — global scope / all accounts

Nel topic `commands` generale, `/trades` deve supportare una vista trasversale su account e trader multipli.

### Header target

```text
📊 TRADES — All accounts
- - - - - - - - - - - - - - - - - - - -
Total: 27   Updated: 14:32:05
Filters: All accounts · All traders
Order: Updated desc
- - - - - - - - - - - - - - - - - - - -
```

### Template item target

```text
#5 · BTC/USDT · LONG · PARTIALLY_CLOSED
Trader: trader_devos_crypto · Account: demo_2
uPnL: +34.20 USDT  rPnL: +14.20 USDT
Details: /trade #5
```

### Esempio completo

```text
📊 TRADES — All accounts
- - - - - - - - - - - - - - - - - - - -
Total: 27   Updated: 14:32:05
Filters: All accounts · All traders
Order: Updated desc
- - - - - - - - - - - - - - - - - - - -
#5 · BTC/USDT · LONG · PARTIALLY_CLOSED
Trader: trader_devos_crypto · Account: demo_2
uPnL: +34.20 USDT  rPnL: +14.20 USDT
Details: /trade #5
- - - - - - - - - - - - - - - - - - - -
#17 · ETH/USDT · SHORT · OPEN
Trader: trader_alpha · Account: demo_1
uPnL: -3.20 USDT  rPnL: -0.20 USDT
Details: /trade #17
- - - - - - - - - - - - - - - - - - - -
#22 · SOL/USDT · LONG · WAITING_ENTRY
Trader: trader_beta · Account: demo_3
rPnL: —
Details: /trade #22
```

### Regole `global scope`

- target `4 righe` per item
- ordine di default consigliato: `Updated desc`
- seconda riga dedicata a `Trader + Account`
- non comprimere `Trader + Account` nella prima riga
- filtri globali primari: `Account`, `Trader`

### Variante ordinamento cronologico

Se il comando usa un ordinamento cronologico esplicito per aggiornamento chain, l'header deve dichiararlo.

```text
📊 TRADES — All accounts
- - - - - - - - - - - - - - - - - - - -
Total: 27   Updated: 14:32:05
Filters: All accounts · All traders
Order: Chain updated desc
- - - - - - - - - - - - - - - - - - - -
#5 · BTC/USDT · LONG · PARTIALLY_CLOSED
Trader: trader_devos_crypto · Account: demo_2
uPnL: +34.20 USDT  rPnL: +14.20 USDT
Details: /trade #5
- - - - - - - - - - - - - - - - - - - -
#44 · XRP/USDT · SHORT · OPEN
Trader: trader_gamma · Account: demo_1
uPnL: +4.10 USDT  rPnL: +0.00 USDT
Details: /trade #44
```

---

## `/trades` empty state

```text
📊 TRADES — demo_1
- - - - - - - - - - - - - - - - - - - -
Total: 0   Updated: 14:32:05
- - - - - - - - - - - - - - - - - - - -
No trades in scope.
```

---

## `/trade #n`

## Scopo

`/trade #n` e` il dettaglio completo del trade.

Deve rendere leggibile:

- stato attuale;
- contesto operativo;
- struttura ordini;
- metriche correnti o finali;
- azioni ancora disponibili;
- cronologia completa degli eventi;
- collegamento ai `clean_log` sorgente.

## Struttura fissa

Ogni `/trade #n` usa questo ordine:

```text
1. Titolo trade
2. Meta info
3. Struttura ordine (entry / tp / sl / be)
4. Stato economico corrente oppure final result
5. Actions, solo se il trade e` ancora azionabile
6. Timeline eventi
```

## Comportamento `/trade #n` in global scope

`/trade #n` non cambia struttura in global scope.

Una volta aperto il dettaglio di una chain, il messaggio torna sempre mono-trade e deve mostrare chiaramente:

- `Trader`
- `Exchange Account`

Quindi il global scope impatta `/trades`, ma non richiede un template diverso per `/trade #n`.

---

## `/trade #n` — trade aperto / partial

```text
#5 · BTC/USDT · LONG · PARTIALLY_CLOSED
- - - - - - - - - - - - - - - - - - - -
Trader: trader_devos_crypto
Exchange Account: demo_2
Updated: 14:32:05
- - - - - - - - - - - - - - - - - - - -
Entry: 63,500 ✓ · 63,200 ✗ · 62,800 ✗
TP:    64,000 ✓ · 65,200 · 66,500
SL:    62,000 · BE: No
uPnL:  +34.20 USDT  rPnL:  +14.20 USDT
- - - - - - - - - - - - - - - - - - - -
Actions: /cancel #5 · /close #5
- - - - - - - - - - - - - - - - - - - -
Events:
• SIGNAL ACCEPTED · 14 Jun 09:10:00
  Source: Signal -> clean_log
                                          // separatore una riga vuota
• ENTRY OPENED · 14 Jun 09:10:00
  Source: exchange -> clean_log

• TP1 FILLED · 14 Jun 09:10:01
  Source: exchange -> clean_log

• UPDATE DONE · 14 Jun 09:10:02
  Type: CANCEL_PENDING
  Source: operation_rules -> clean_log
```

### Regole

- `Updated` = ultimo aggiornamento utile del trade
- se il trade e` ancora aperto, mostrare `uPnL` e `rPnL`
- `Actions` presente solo se il trade e` ancora azionabile
- ogni evento deve contenere il link al relativo `clean_log` quando disponibile

---

## `/trade #n` — `WAITING_ENTRY`

```text
#5 · BTC/USDT · LONG · WAITING_ENTRY
- - - - - - - - - - - - - - - - - - - -
Trader: trader_devos_crypto
Exchange Account: demo_2
Updated: 14:32:05
- - - - - - - - - - - - - - - - - - - -
Entry: 63,500 · 63,200 · 62,800
TP:    64,000 · 65,200 · 66,500
SL:    62,000 · BE: No
- - - - - - - - - - - - - - - - - - - -
Actions: /cancel #5 · /close #5
- - - - - - - - - - - - - - - - - - - -
Events:
• SIGNAL ACCEPTED · 14 Jun 09:10:00
  Source: Signal -> clean_log
```

### Regole `WAITING_ENTRY`

- niente `uPnL`
- niente `rPnL` obbligatorio
- la sezione economica puo` essere omessa del tutto
- `Actions` resta visibile se il trade e` cancellabile/chiudibile

---

## `/trade #n` — position closed

```text
#5 · BTC/USDT · LONG · POSITION CLOSED
- - - - - - - - - - - - - - - - - - - -
Trader: trader_devos_crypto
Exchange Account: demo_2
Updated: 14:32:05
- - - - - - - - - - - - - - - - - - - -
Entry: 63,500 ✓ · 63,200 ✗ · 62,800 ✗
TP:    64,000 ✓ · 65,200 ✓
SL:    62,000 · BE: No
- - - - - - - - - - - - - - - - - - - -
Final Result:
ROI net: +3.67% · RoR: +9.12% · R: +0.22R
PnL net: +44.17 USDT · PnL gross: +45.20 USDT
Fees: -2.06 USDT · Funding: +0.03 USDT
- - - - - - - - - - - - - - - - - - - -
Events:
• SIGNAL ACCEPTED · 14 Jun 09:10:00
  Source: Signal -> clean_log

• ENTRY OPENED · 14 Jun 09:10:00
  Source: exchange -> clean_log

• TP1 FILLED · 14 Jun 09:10:01
  Source: exchange -> clean_log

• UPDATE DONE · 14 Jun 09:10:02
  Type: CANCEL_PENDING
  Source: operation_rules -> clean_log

• POSITION CLOSED · 14 Jun 09:10:02
  Reason: FINAL TP FILLED
  Source: exchange -> clean_log
```

### Regole trade chiuso

- niente `Actions`
- niente `uPnL / rPnL` correnti
- la sezione economica viene sostituita da `Final Result`
- `Final Result` e` la sezione primaria per la performance del trade

---

## `/trade #n` — cancelled without fill

```text
#24 · ETH/USDT · LONG · CANCELLED_UNFILLED
- - - - - - - - - - - - - - - - - - - -
Trader: trader_devos_crypto
Exchange Account: demo_2
Updated: 14:32:05
- - - - - - - - - - - - - - - - - - - -
Entry: 2,140 · 2,120
TP:    2,180 · 2,220
SL:    2,090 · BE: No
- - - - - - - - - - - - - - - - - - - -
Final Result:
PnL: No fill
- - - - - - - - - - - - - - - - - - - -
Events:
• SIGNAL ACCEPTED · 14 Jun 16:12:00
  Source: Signal -> clean_log

• UPDATE DONE · 14 Jun 16:14:10
  Type: CANCEL_PENDING
  Source: operation_rules -> clean_log

• POSITION CANCELLED · 14 Jun 16:14:12
  Reason: CANCEL_PENDING
  Source: exchange -> clean_log
```

### Regole `CANCELLED_UNFILLED`

- e` terminale
- niente `Actions`
- niente metriche PnL reali di posizione
- il risultato finale e` esplicitamente `No fill`

---

## `/trade #n` — blocked / review required

```text
#7 · ETH/USDT · LONG · REVIEW_REQUIRED
- - - - - - - - - - - - - - - - - - - -
Trader: trader_devos_crypto
Exchange Account: demo_2
Updated: 14:32:05
- - - - - - - - - - - - - - - - - - - -
Entry: 2,140 ✓
TP:    2,180 · 2,220
SL:    —
uPnL:  -3.20 USDT  rPnL:  0.00 USDT
- - - - - - - - - - - - - - - - - - - -
Actions: /close #7
- - - - - - - - - - - - - - - - - - - -
Events:
• SIGNAL ACCEPTED · 14 Jun 11:50:00
  Source: Signal -> clean_log

• ENTRY OPENED · 14 Jun 11:52:00
  Source: exchange -> clean_log

• REVIEW REQUIRED · 14 Jun 11:52:05
  Reason: missing_sl
  Source: system -> clean_log
```

---

## Timeline eventi

## Principi

- ordine cronologico crescente
- ogni evento e` una unita` audit autonoma
- ogni evento deve essere comprensibile anche fuori dal contesto del trade summary

## Campi minimi evento

```text
• <EVENT LABEL> · <timestamp>
  Source: <source> -> clean_log
```

## Campi opzionali

```text
  Type: <update type>
  Reason: <reason>
  Note: <extra detail>
```

## Eventi tipici attesi

- `SIGNAL ACCEPTED`
- `ENTRY OPENED`
- `ENTRY PARTIALLY FILLED`
- `TP1 FILLED`
- `TP2 FILLED`
- `SL MOVED TO BE`
- `UPDATE DONE`
- `REVIEW REQUIRED`
- `POSITION CLOSED`
- `POSITION CANCELLED`

---

## Linking a `clean_log`

## Requisito

Ogni evento deve puntare al suo messaggio `clean_log` quando esiste.

In particolare:

- `Source: Signal` deve sempre referenziare il `clean_log` del segnale accettato
- eventi exchange devono linkare il `clean_log` operativo corrispondente
- eventi `operation_rules` devono linkare il `clean_log` dell'update eseguito
- eventi system/review devono linkare il `clean_log` diagnostico o di review, se presente

## Fallback

Se il link non esiste ancora, l'evento resta visibile come testo.
Il documento UI pero` assume come target finale la disponibilita` del link.

---

## Filtri suggeriti per `/trades` in global scope

```text
[Account ▸]  [Trader ▸]  [Status ▸]
[Side ▸]     [Order ▸]   [← Back]
```

Selector account:

```text
[All accounts]
[demo_1]  [demo_2]  [demo_3]
[← Back]
```

Selector trader:

```text
[All traders]
[trader_a]  [trader_b]  [trader_c]
[← Back]
```

---

## Criteri di accettazione

1. `/trades` mostra solo informazione sintetica, con CTA naturale a `/trade #n`.
2. Ogni item `/trades` usa il formato compatto a 3 righe target.
3. `/trade #n` resta un singolo messaggio completo.
4. `/trade #n` mostra sempre meta info, struttura ordine e timeline eventi.
5. I trade aperti mostrano metriche correnti e azioni disponibili.
6. I trade chiusi o cancellati non mostrano azioni e usano `Final Result`.
7. `WAITING_ENTRY` non mostra PnL come se la posizione fosse aperta.
8. Ogni evento della timeline linka il relativo `clean_log` quando disponibile.
9. `Source: Signal` e` obbligatoriamente collegato al `clean_log` sorgente.
