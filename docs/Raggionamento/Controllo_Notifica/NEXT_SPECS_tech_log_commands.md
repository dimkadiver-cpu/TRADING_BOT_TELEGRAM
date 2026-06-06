# Spec da fare — Tech_Log e Commands

Documento di contesto per future sessioni. Obiettivo: definire le spec per Tech_Log e Commands
seguendo lo stesso principio block-based DSL usato per clean_log.

**Riferimento obbligatorio prima di iniziare:**
- DSL spec: `docs/superpowers/specs/2026-06-06-log-templating-design.md`
- Esempi clean_log: `docs/Raggionamento/Controllo_Notifica/Template_clean_log/`

---

## Perché separate da clean_log

| Dimensione | clean_log | Tech_Log | Commands |
|------------|-----------|----------|----------|
| Trigger | event-push (runtime emette) | event-push (runtime emette) | request-response (operatore chiede) |
| Audience | trader (risultati trade) | dev / operatori (debug) | trader / operatori |
| Dati | lifecycle, exchange fills | worker state, errors | status queries, DB views |
| Canale | clean_log topic | tech_log topic | risposta diretta al comando |

Stessa DSL (`_blocks.py`), template separati, `TemplateConfig` separato per tipo.

---

## A — Tech_Log

### Stato attuale

File: `src/runtime_v2/control_plane/formatters/tech_log.py` — 38 righe

```python
def format_tech_log(payload: dict, *, delivery_mode: str = "supergroup_topics") -> str:
    level = str(payload.get("level", "INFO")).upper()
    category = payload.get("category") or "Runtime"
    title = payload.get("title") or ""
    description = payload.get("description") or ""
    source = payload.get("source")
    context = payload.get("context")  # dict | None
    action = payload.get("action")    # str | None
    ...
```

Campi attuali nel payload: `level`, `category`, `title`, `description`, `context` (dict), `action`, `source`.
`delivery_mode`: `"supergroup_topics"` (default) o `"private_bot"` (aggiunge prefix `⚠️ --SYSTEM--`).

Output attuale (supergroup):
```
[ERROR] Exchange: connection_failed
────────────────
Could not connect to exchange after 3 retries.

Context:
account_id: bybit_main
symbol: BTC/USDT
attempt: 3

Action: retry_scheduled_in_60s
────────────────
Source: exchange_connector
```

### Cosa definire nella spec

**1. È sufficiente un unico template?**

Il template attuale è generico: funziona per tutti i livelli/categorie. La domanda è se alcune
categorie hanno layout strutturalmente diverso o se basta rendere il template block-based.

Ipotesi: unico `_TECH_LOG_BLOCKS` con `ConditionalBlock` per context, action, delivery_mode.
Un template per tipo di messaggio sarebbe overkill — le categorie variano per payload, non struttura.

**2. `context` dict → rendering fisso o dinamico?**

Attualmente le chiavi di `context` variano per ogni evento. Il block-based può gestirlo con
un `DerivedBlock` che itera le chiavi, mantenendo la logica `display_symbol` per `symbol`.

**3. `delivery_mode` → come gestirlo?**

Opzione A: `payload_transform` inietta `_prefix` in base a `delivery_mode`
Opzione B: `BranchBlock` dentro i blocks
Opzione C: lasciare fuori dai blocks — gestito nel dispatcher

**4. Categorie da inventariare**

Prima di scrivere la spec, inventariare tutti i siti di chiamata a `format_tech_log()` nel
codebase per capire quante categorie esistono, quali campi usano, e se ci sono pattern anomali.

```
grep -r "format_tech_log\|tech_log" src/runtime_v2/ --include="*.py"
```

### Formato output target (ipotesi)

```
[ERROR] Exchange: connection_failed
- - -
Could not connect after 3 retries.
- - -
Context:
account_id: bybit_main
symbol: BTC/USDT
attempt: 3
- - -
Action: retry_in_60s
- - -
Source: exchange_connector
```

Differenza da clean_log: separatore `- - -` con spazi diverso? TBD.

### Domande aperte

- Mantenere `────────────────` (largo) o uniformare a `- - -` (clean_log)?
- `[LEVEL] Category: title` rimane header fisso o diventa `HeaderBlock` con emoji per livello?
- `context` dict: rendere le chiavi case-friendly (es. `account_id` → `Account`)?
- `delivery_mode` deve sopravvivere nella nuova arch o va rimosso?

---

## B — Commands

### Stato attuale — mappa formatter

| File | Funzione(i) | Trigger | Output |
|------|-------------|---------|--------|
| `status.py` | `format_status(view)` | `/status` | Sezioni: Mode, Trades, Execution, Risk + hints |
| `health.py` | `format_health(view)` | `/health` | Workers list + DB + Exchange |
| `pnl.py` | `format_pnl(view)` | `/pnl` | Account data + chain counts + unavailable items |
| `trades.py` | (tabella) | `/trades` | Colonne allineate: ID/Symbol/Side/State/Protection |
| `trade_detail.py` | `format_trade_detail(detail)` | `/trade #id` | Position detail con separatore adattivo |
| `reviews.py` | `format_reviews(view)` | `/reviews` | Lista pending reviews |
| `control.py` | `format_control(view)` | `/control` | Blocks attivi + blacklist |
| `pause.py` | `format_pause`, `format_resume`, `format_start` | `/pause`, `/resume` | Conferma azione + effetto |
| `debug.py` | `format_debug_on`, `format_debug_off` | `/debug` | Conferma debug mode |

### Caratteristiche distintive

**`status.py`** — già sections-based, hint lines hardcoded (`/trades`, `/reviews`, `/control`).
`status_level()` calcola emoji da `StatusView` — logica che va nel `payload_transform`.

**`trades.py`** — tabella colonne allineate. Il block-based non gestisce nativamente tabelle
columnar; richiede un `TableBlock` apposito o restare hardcoded per questo caso.

**`pnl.py`** — sezione `"Unavailable in current persistence:"` con items statici hardcoded.
Questo pattern suggerisce `StaticBlock` con testo multi-riga, o `ListBlock` di `StaticBlock`.

**`trade_detail.py`** — usa `_SEP` dinamico che adatta la larghezza al contenuto più lungo.
È l'unico formatter con separatore adattivo — lo stesso meccanismo di `_finalize()` già in `clean_log`.

**`pause.py`** — ha doppio path (legacy con `PauseResult`, nuovo con keyword args). Il legacy
path va rimosso prima di blockeizzare, o gestito come caso speciale.

**`reviews.py`** e **`control.py`** — list-based già semplici. Candidati naturali per `ListBlock`.

**`debug.py`** — 2 funzioni, nessun condizionale, struttura minimalista. Potrebbe non valere
la migrazione al DSL — verificare se il guadagno è reale.

### Ordine di priorità suggerito

1. **`status.py`** — più usato, più critico, già strutturato per sections → migrazione pulita
2. **`trade_detail.py`** — usa già `_finalize()` compatibile → portarlo a block-based è naturale
3. **`reviews.py`** + **`control.py`** — semplici, bassa priorità
4. **`health.py`** + **`pnl.py`** — sezioni fisse, migrazione meccanica
5. **`pause.py`** — rimuovere il legacy path prima; poi struttura semplice
6. **`trades.py`** — tabella colonne: decidere se introduce `TableBlock` o si esclude dal DSL
7. **`debug.py`** — da valutare se vale la migrazione

### Domanda aperta principale: tabelle columnar

`/trades` produce output tipo:
```
#6  WLD/USDT  LONG   OPEN    SL: 0.280
#7  BTC/USDT  LONG   PARTIAL SL: 64,000
```

Questo layout dipende da allineamento colonne calcolato sul set completo di righe.
Opzioni:
- `TableBlock` con colonne e formatter — estende il DSL
- `DerivedBlock` che riceve la lista e produce il testo già formattato — no nuovo block type
- Restare fuori dal DSL per `trades.py` — accettare eccezione

### Domande aperte

- I commands usano `────────────────` (lungo) — uniformare a `- - -` o mantenere stile diverso per distinzione visiva?
- I hint lines (`/trades`, `/reviews`) vanno in `FooterBlock` o in `StaticBlock`?
- `format_pause` legacy path — quando rimuovere?
- `/debug` — vale il refactor o tenere le 2 funzioni hardcoded?
- `/pnl` mostra `"Unavailable in current persistence:"` — queste voci diventeranno disponibili? Definire chi aggiorna questa sezione quando i dati arrivano.

---

## Architettura target condivisa

```
src/runtime_v2/control_plane/formatters/
├── _blocks.py                      ← CONDIVISO — Block types + render_template() + _finalize()
├── templates/
│   ├── clean_log.py                ← TEMPLATE_REGISTRY clean_log (già definito in spec)
│   ├── tech_log.py                 ← NEW — TemplateConfig per tech log
│   └── commands.py                 ← NEW — TemplateConfig per ogni comando
├── clean_log.py                    ← thin dispatcher (~20 righe)
├── tech_log.py                     ← thin dispatcher o singola funzione residua
├── status.py                       ← thin dispatcher o refactor diretto
└── ...
```

`_blocks.py` è già il prossimo step di implementazione (Priorità 1 nell'HANDOFF).
Tech_Log e Commands vengono dopo — usano lo stesso file `_blocks.py` senza modifiche.

---

## Prompt per la sessione Tech_Log

```
Obiettivo: scrivere la spec per il formatter tech_log block-based.

Contesto:
- DSL reference: docs/superpowers/specs/2026-06-06-log-templating-design.md
- File attuale: src/runtime_v2/control_plane/formatters/tech_log.py (38 righe)
- Planning doc: docs/Raggionamento/Controllo_Notifica/NEXT_SPECS_tech_log_commands.md

Prima di scrivere la spec:
1. Greppare tutti i siti di chiamata format_tech_log() nel codebase per inventariare categorie
2. Verificare se esiste una enum/costante per i livelli log (INFO/WARNING/ERROR/DEBUG)
3. Decidere: un template unico vs template per categoria

Output atteso:
- Spec salvata in docs/superpowers/specs/2026-06-07-tech-log-templating-design.md
- Esempi in docs/Raggionamento/Controllo_Notifica/Template_tech_log/ (da creare)
```

## Prompt per la sessione Commands

```
Obiettivo: scrivere la spec per i command formatter block-based.

Contesto:
- DSL reference: docs/superpowers/specs/2026-06-06-log-templating-design.md
- Formatters attuali: src/runtime_v2/control_plane/formatters/ (status, health, pnl, trades, ecc.)
- Planning doc: docs/Raggionamento/Controllo_Notifica/NEXT_SPECS_tech_log_commands.md

Prima di scrivere la spec:
1. Leggere tutti i formatter attuali per catturare la struttura esatta
2. Decidere la strategia per trades.py (tabella columnar) — TableBlock o eccezione?
3. Decidere se uniformare il separatore (────────────────) o mantenerlo distinto da clean_log

Output atteso:
- Spec salvata in docs/superpowers/specs/2026-06-07-commands-templating-design.md
- Esempi in docs/Raggionamento/Controllo_Notifica/Template_commands/ (da creare)
- Nota: non implementare — solo spec e esempi. Implementazione viene dopo _blocks.py e clean_log.
```
