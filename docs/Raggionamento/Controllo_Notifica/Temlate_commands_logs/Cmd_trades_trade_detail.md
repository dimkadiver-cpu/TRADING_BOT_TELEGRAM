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
- cronologia completa degli eventi principali;
- collegamento ai `clean_log` sorgente.

## Struttura fissa

Ogni `/trade #n` usa questo ordine di 6 sezioni. Le sezioni condizionali compaiono solo se applicabili allo stato corrente:

```text
1. Titolo trade          → sempre presente
2. Meta info             → sempre presente
3. Setup ordine          → sempre presente (Entry / TP / SL / BE)
4. Stato economico       → condizionale (vedi regole)
5. Actions               → solo se il trade e` azionabile
6. Timeline eventi       → sempre presente
```

### Regole condizionali per sezione economica

| Stato | Sezione economica |
|---|---|
| `WAITING_ENTRY` | assente |
| `OPEN` | `uPnL` e `rPnL` live |
| `PARTIALLY_CLOSED` | `uPnL` e `rPnL` live |
| `REVIEW_REQUIRED` | `uPnL` e `rPnL` live |
| `POSITION_CLOSED` | `Final Result` con metriche complete |
| `CANCELLED_UNFILLED` | `Final Result: PnL: No fill` |

La sezione economica non compare mai vuota: o mostra metriche live, o mostra il risultato finale, o e` assente.

### Matrice azioni per stato

| Stato | `/cancel_n` | `/close_n` |
|---|---|---|
| `WAITING_ENTRY` | ✓ | ✗ |
| `OPEN` | ✓ | ✓ |
| `PARTIALLY_CLOSED` | ✓ | ✓ |
| `REVIEW_REQUIRED` | ✗ | ✓ |
| `POSITION_CLOSED` | ✗ | ✗ |
| `CANCELLED_UNFILLED` | ✗ | ✗ |

- `/cancel_n` = ci sono ordini pending da cancellare
- `/close_n` = c'e` una posizione aperta da chiudere
- ordine fisso: `/cancel_n` sempre prima di `/close_n`
- se nessuna azione e` disponibile, la sezione Actions non compare

## Comportamento `/trade #n` in global scope

`/trade #n` non cambia struttura in global scope.

Una volta aperto il dettaglio di una chain, il messaggio torna sempre mono-trade e deve mostrare chiaramente:

- `Trader`
- `Exchange Account`

Quindi il global scope impatta `/trades`, ma non richiede un template diverso per `/trade #n`.

---

## Sezione 2 — Setup ordine

I livelli Entry, TP e SL usano tre stati visivi:

| Marcatore | Significato |
|---|---|
| ✓ | filled / colpito |
| ✗ | cancellato / saltato |
| *(nessuno)* | pending / ancora aperto |

### BE (Break Even)

- Inattivo → `BE: No`
- Attivo → `SL: — · BE: <prezzo>` (SL originale scompare, prezzo BE esplicito)

### Esempi setup per stato

```text
// WAITING_ENTRY — tutto pending, nessun marcatore
Entry: 63,500 · 63,200 · 62,800
TP:    64,000 · 65,200 · 66,500
SL:    62,000 · BE: No

// OPEN / PARTIALLY_CLOSED — mix ✓ ✗ e pending
Entry: 63,500 ✓ · 63,200 ✗ · 62,800 ✗
TP:    64,000 ✓ · 65,200 · 66,500
SL:    62,000 · BE: No

// BE attivo
Entry: 63,500 ✓ · 63,200 ✗ · 62,800 ✗
TP:    64,000 ✓ · 65,200 · 66,500
SL:    — · BE: 63,500

// REVIEW_REQUIRED — SL mancante
Entry: 2,140 ✓
TP:    2,180 · 2,220
SL:    —

// CANCELLED_UNFILLED — nessun fill, nessun marcatore
Entry: 2,140 · 2,120
TP:    2,180 · 2,220
SL:    2,090 · BE: No
```

---

## `/trade #n` — `WAITING_ENTRY`

```text
#9 · BTC/USDT · LONG · WAITING_ENTRY
- - - - - - - - - - - - - - - - - - - -
Trader: trader_devos_crypto
Exchange Account: demo_2
Updated: 14:32:05
- - - - - - - - - - - - - - - - - - - -
Entry: 63,500 · 63,200 · 62,800
TP:    64,000 · 65,200 · 66,500
SL:    62,000 · BE: No
- - - - - - - - - - - - - - - - - - - -
Actions: /cancel_9
- - - - - - - - - - - - - - - - - - - -
Events:
• SIGNAL ACCEPTED · 14 Jun 09:10:00
  Source: Signal → [clean_log](url)
```

### Regole `WAITING_ENTRY`

- sezione economica assente
- `Actions`: solo `/cancel_n`
- nessun marcatore ✓/✗ nei livelli

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
- - - - - - - - - - - - - - - - - - - -
uPnL:  +34.20 USDT  rPnL:  +14.20 USDT
- - - - - - - - - - - - - - - - - - - -
Actions: /cancel_5 · /close_5
- - - - - - - - - - - - - - - - - - - -
Events:
• SIGNAL ACCEPTED · 14 Jun 09:10:00
  Source: Signal → [clean_log](url)

• ENTRY OPENED · 14 Jun 09:10:05
  Source: exchange → [clean_log](url)

• TP1 FILLED · 14 Jun 09:15:20
  Source: exchange → [clean_log](url)

• UPDATE DONE · 14 Jun 09:20:00
  Type: CANCEL_PENDING
  Source: operation_rules → [clean_log](url)
```

### Regole trade aperto

- `Updated` = ultimo aggiornamento utile del trade
- `uPnL` e `rPnL` live
- `Actions`: `/cancel_n · /close_n`

---

## `/trade #n` — trade aperto con BE attivo

```text
#5 · BTC/USDT · LONG · OPEN
- - - - - - - - - - - - - - - - - - - -
Trader: trader_devos_crypto
Exchange Account: demo_2
Updated: 14:32:05
- - - - - - - - - - - - - - - - - - - -
Entry: 63,500 ✓ · 63,200 ✗ · 62,800 ✗
TP:    64,000 ✓ · 65,200 · 66,500
SL:    — · BE: 63,500
- - - - - - - - - - - - - - - - - - - -
uPnL:  +18.40 USDT  rPnL:  +14.20 USDT
- - - - - - - - - - - - - - - - - - - -
Actions: /cancel_5 · /close_5
- - - - - - - - - - - - - - - - - - - -
Events:
• SIGNAL ACCEPTED · 14 Jun 09:10:00
  Source: Signal → [clean_log](url)

• ENTRY OPENED · 14 Jun 09:10:05
  Source: exchange → [clean_log](url)

• TP1 FILLED · 14 Jun 09:15:20
  Source: exchange → [clean_log](url)

• SL MOVED TO BE · 14 Jun 09:16:00
  Source: operation_rules → [clean_log](url)
```

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
  Source: Signal → [clean_log](url)

• ENTRY OPENED · 14 Jun 09:10:05
  Source: exchange → [clean_log](url)

• TP1 FILLED · 14 Jun 09:15:20
  Source: exchange → [clean_log](url)

• UPDATE DONE · 14 Jun 09:20:00
  Type: CANCEL_PENDING
  Source: operation_rules → [clean_log](url)

• POSITION CLOSED · 14 Jun 09:25:00
  Reason: FINAL TP FILLED
  Source: exchange → [clean_log](url)
```

### Regole trade chiuso

- niente `Actions`
- niente `uPnL / rPnL` correnti
- `Final Result` sostituisce la sezione economica

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
  Source: Signal → [clean_log](url)

• UPDATE DONE · 14 Jun 16:14:10
  Type: CANCEL_PENDING
  Source: operation_rules → [clean_log](url)

• POSITION CANCELLED · 14 Jun 16:14:12
  Reason: CANCEL_PENDING
  Source: exchange → [clean_log](url)
```

### Regole `CANCELLED_UNFILLED`

- stato terminale
- niente `Actions`
- niente marcatori ✓/✗ nei livelli (nessun fill avvenuto)
- `Final Result: PnL: No fill`

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
- - - - - - - - - - - - - - - - - - - -
uPnL:  -3.20 USDT  rPnL:  0.00 USDT
- - - - - - - - - - - - - - - - - - - -
Actions: /close_7
- - - - - - - - - - - - - - - - - - - -
Events:
• SIGNAL ACCEPTED · 14 Jun 11:50:00
  Source: Signal → [clean_log](url)

• ENTRY OPENED · 14 Jun 11:52:00
  Source: exchange → [clean_log](url)

• REVIEW REQUIRED · 14 Jun 11:52:05
  Reason: missing_sl
  Source: system → [clean_log](url)
```

### Regole `REVIEW_REQUIRED`

- posizione aperta: mostra `uPnL` e `rPnL`
- `Actions`: solo `/close_n` (non cancellabile in stato review)
- `SL: —` se SL mancante (trigger del review)

---

## Timeline eventi

## Principi

- ordine cronologico crescente
- compaiono solo gli eventi con `is_main_event: true` nel log
- gli eventi interni senza flag non vengono mostrati
- ogni evento e` una unita` audit autonoma
- riga vuota tra eventi

## Campi minimi evento

```text
• <EVENT LABEL> · <timestamp>
  Source: <sorgente> → [clean_log](url)
```

## Campi opzionali

```text
  Type: <update type>
  Reason: <reason>
  Note: <extra detail>
```

## Fallback link assente

Se il `clean_log` non e` ancora disponibile, l'evento resta visibile come testo senza link:

```text
• ENTRY OPENED · 14 Jun 09:10:05
  Source: exchange
```

## Sorgenti possibili

| Source | Significato |
|---|---|
| `Signal` | segnale di origine ricevuto |
| `exchange` | evento proveniente dall'exchange |
| `operation_rules` | aggiornamento eseguito dalle regole operative |
| `system` | evento generato internamente dal sistema |

## Eventi principali attesi

- `SIGNAL ACCEPTED`
- `ENTRY OPENED`
- `ENTRY PARTIALLY FILLED`
- `TP1 FILLED` / `TP2 FILLED` / ...
- `SL MOVED TO BE`
- `UPDATE DONE`
- `REVIEW REQUIRED`
- `POSITION CLOSED`
- `POSITION CANCELLED`

---

## Linking a `clean_log`

## Requisito

Ogni evento con `is_main_event: true` deve puntare al suo `clean_log` con un link inline cliccabile:

```text
Source: Signal → [clean_log](url)
Source: exchange → [clean_log](url)
```

Il `clean_log` e` un meta-link che apre il messaggio di log dedicato nel topic appropriato.

In particolare:

- `Source: Signal` deve sempre referenziare il `clean_log` del segnale accettato
- eventi exchange devono linkare il `clean_log` operativo corrispondente
- eventi `operation_rules` devono linkare il `clean_log` dell'update eseguito
- eventi system/review devono linkare il `clean_log` diagnostico o di review, se presente

## Fallback

Se il link non esiste ancora, l'evento resta visibile come testo senza link.
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
5. I trade aperti mostrano `uPnL` / `rPnL` live e le azioni disponibili secondo la matrice stati.
6. I trade chiusi o cancellati non mostrano azioni e usano `Final Result`.
7. `WAITING_ENTRY` non mostra sezione economica e ha solo `/cancel_n` come azione.
8. `REVIEW_REQUIRED` ha solo `/close_n` come azione.
9. BE attivo mostra `SL: — · BE: <prezzo>`.
10. La timeline mostra solo eventi con `is_main_event: true` nel log.
11. Ogni evento della timeline linka il relativo `clean_log` con link inline cliccabile.
12. `Source: Signal` e` obbligatoriamente collegato al `clean_log` sorgente.
13. Se il `clean_log` non e` disponibile, l'evento resta senza link (nessun testo placeholder).
