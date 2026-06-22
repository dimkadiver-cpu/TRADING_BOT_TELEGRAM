# Design Spec — `/trade #n` Trade Detail

**Data:** 2026-06-22
**Argomento:** Design, layout e funzionalità del comando `/trade #n`
**Documento operativo:** `docs/Raggionamento/Controllo_Notifica/Temlate_commands_logs/Cmd_trades_trade_detail.md`

---

## Contesto

`/trade #n` è il dettaglio completo di un singolo trade nel bot Telegram di trading.
È la rappresentazione narrativa del ciclo di vita del trade, dalla ricezione del segnale fino a chiusura, cancellazione o blocco.

Complementa `/trades` (lista sintetica) come vista audit completa.

---

## Decisioni di design

### 1. Struttura fissa a 6 sezioni condizionali

Il template mantiene sempre lo stesso ordine di 6 sezioni. Le sezioni condizionali compaiono o si trasformano in base allo stato del trade.

```
1. Titolo trade          → sempre presente
2. Meta info             → sempre presente
3. Setup ordine          → sempre presente
4. Stato economico       → condizionale per stato
5. Actions               → solo se azionabile
6. Timeline eventi       → sempre presente
```

### 2. Sezione setup — marcatori livelli

| Marcatore | Significato |
|---|---|
| ✓ | filled / colpito |
| ✗ | cancellato / saltato |
| *(nessuno)* | pending / ancora aperto |

**BE attivo:** `SL: — · BE: <prezzo>` (SL originale scompare, BE con prezzo esplicito)

### 3. Sezione economica — varianti per stato

| Stato | Sezione economica |
|---|---|
| `WAITING_ENTRY` | assente |
| `OPEN` / `PARTIALLY_CLOSED` / `REVIEW_REQUIRED` | `uPnL` e `rPnL` live |
| `POSITION_CLOSED` | `Final Result` con metriche complete |
| `CANCELLED_UNFILLED` | `Final Result: PnL: No fill` |

### 4. Matrice azioni per stato

| Stato | `/cancel_n` | `/close_n` |
|---|---|---|
| `WAITING_ENTRY` | ✓ | ✗ |
| `OPEN` | ✓ | ✓ |
| `PARTIALLY_CLOSED` | ✓ | ✓ |
| `REVIEW_REQUIRED` | ✗ | ✓ |
| `POSITION_CLOSED` | ✗ | ✗ |
| `CANCELLED_UNFILLED` | ✗ | ✗ |

Se nessuna azione disponibile, la sezione Actions non compare.

### 5. Timeline eventi — flag sul log

- Compaiono solo gli eventi con `is_main_event: true` nel log
- Gli eventi interni senza flag non vengono mostrati
- Formato: `• EVENTO · timestamp` + `Source: sorgente → [clean_log](url)`

### 6. Link clean_log — inline cliccabile

Il `clean_log` è un meta-link inline cliccabile che apre il messaggio di log dedicato nel topic appropriato.

```text
Source: Signal → [clean_log](url)
```

Fallback se link non disponibile: evento visibile come testo senza link.

---

## Approvazione

- Struttura 6 sezioni condizionali: ✓
- Setup ✓/✗/pending + BE con prezzo: ✓
- Sezione economica varianti: ✓
- Matrice azioni per stato: ✓
- Timeline con `is_main_event` flag: ✓
- Link clean_log inline: ✓
