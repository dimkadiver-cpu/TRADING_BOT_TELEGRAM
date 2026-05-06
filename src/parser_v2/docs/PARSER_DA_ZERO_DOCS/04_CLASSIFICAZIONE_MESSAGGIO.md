# Logica di classificazione del messaggio

## Obiettivo

La classificazione deve rispondere a una domanda semplice:

```text
Che tipo di messaggio è?
```

Valori:

```text
SIGNAL
UPDATE
REPORT
INFO
```

\---

## Regola principale

La classificazione non deve essere basata solo sui marker.

Ordine corretto:

```text
1. struttura segnale
2. intenti UPDATE
3. intenti REPORT
4. marker INFO
5. fallback UNCLASSIFIED
```

\---

# 1\. SIGNAL

Un messaggio è `SIGNAL` se contiene struttura da nuovo setup.

## Campi principali

```text
symbol
side
entry
stop\_loss
take\_profit
```

## Completo

```text
primary\_class = SIGNAL
parse\_status = PARSED
```

se ha:

```text
symbol + side + entries + stop\_loss + take\_profits
```

Esempio:

```text
ETHUSDT LONG
Вход лимиткой 2114
Stop 2100
TP1 2128
TP2 2141
```

Output:

```json
{
  "primary\_class": "SIGNAL",
  "parse\_status": "PARSED"
}
```

\---

## Parziale

```text
primary\_class = SIGNAL
parse\_status = PARTIAL
```

se è chiaramente un setup ma mancano campi.

Esempio:

```text
ETHUSDT LONG
Вход лимиткой 2114
Stop 2100
Тейки позже
```

Output:

```json
{
  "primary\_class": "SIGNAL",
  "parse\_status": "PARTIAL",
  "signal": {
    "missing\_fields": \["take\_profits"]
  }
}
```

\---

## Regola critica

Non classificare come `UPDATE` un messaggio che contiene struttura da setup incompleto.

Questo è sbagliato:

```text
entry + stop senza TP
→ UPDATE
```

Questo è corretto:

```text
entry + stop senza TP
→ SIGNAL / PARTIAL
```

\---

# 2\. UPDATE

Un messaggio è `UPDATE` se contiene intenti operativi.

Intenti UPDATE:

```text
MOVE\_STOP\_TO\_BE
MOVE\_STOP
CLOSE\_FULL
CLOSE\_PARTIAL
CANCEL\_PENDING
INVALIDATE\_SETUP
REENTER
ADD\_ENTRY
MODIFY\_ENTRY
MODIFY\_TARGETS
```

Esempio:

```text
стоп в бу
```

Output:

```json
{
  "primary\_class": "UPDATE",
  "primary\_intent": "MOVE\_STOP\_TO\_BE"
}
```

\---

## UPDATE senza target

Il parser deve comunque produrre `UPDATE`.

Non deve degradare a `INFO` solo perché manca target.

Esempio:

```text
стоп в бу
```

Senza reply/link:

```json
{
  "primary\_class": "UPDATE",
  "parse\_status": "PARSED",
  "warnings": \["update\_without\_target\_hint"]
}
```

La validazione successiva deciderà se è applicabile.

\---

# 3\. REPORT

Un messaggio è `REPORT` se descrive cosa è successo, non cosa fare.

Intenti REPORT:

```text
ENTRY\_FILLED
TP\_HIT
SL\_HIT
EXIT\_BE
REPORT\_RESULT
```

Esempio:

```text
первый тейк взяли
```

Output:

```json
{
  "primary\_class": "REPORT",
  "primary\_intent": "TP\_HIT"
}
```

Esempio:

```text
выбило по стопу
```

Output:

```json
{
  "primary\_class": "REPORT",
  "primary\_intent": "SL\_HIT"
}
```

\---

# 4\. INFO

Un messaggio è `INFO` se non è segnale, non è update, non è report operativo.
Se contiene un marker `info` valido, il runtime lo classifica subito come `INFO` e interrompe il parsing operativo prima di signal/update/report.

Esempi:

```text
Всем привет
Через 10 минут начинаем
Обзор рынка
```

Output:

```json
{
  "primary\_class": "INFO",
  "parse\_status": "PARSED",
  "primary\_intent": "INFO\_ONLY"
}
```

\---

# 5\. UNCLASSIFIED

Se non viene trovata nessuna struttura utile:

```json
{
  "primary\_class": "INFO",
  "parse\_status": "UNCLASSIFIED"
}
```

Non introdurre `primary\_class = UNCLASSIFIED`.

`UNCLASSIFIED` è uno status, non una classe.

\---

# Messaggi compositi

Un messaggio può contenere più intenti compatibili.

Esempio:

```text
первый тейк взяли, стоп в бу
```

Output:

```json
{
  "primary\_class": "UPDATE",
  "primary\_intent": "MOVE\_STOP\_TO\_BE",
  "intents": \[
    {"type": "TP\_HIT", "category": "REPORT"},
    {"type": "MOVE\_STOP\_TO\_BE", "category": "UPDATE"}
  ]
}
```

Regola:

```text
Se c'è almeno un UPDATE, primary\_class = UPDATE.
I REPORT compatibili restano come report payload opzionale.
```

\---

# Precedence primary\_intent

Proposta:

```text
SL\_HIT
EXIT\_BE
TP\_HIT
REPORT\_RESULT
CLOSE\_FULL
CLOSE\_PARTIAL
CANCEL\_PENDING
INVALIDATE\_SETUP
MOVE\_STOP\_TO\_BE
MOVE\_STOP
MODIFY\_TARGETS
MODIFY\_ENTRY
ADD\_ENTRY
REENTER
ENTRY\_FILLED
INFO\_ONLY
```

Nota: se preferisci che i comandi operativi dominino sempre sui report, sposta gli UPDATE sopra i REPORT.

Io terrei questa logica:

```text
primary\_class domina per categoria
primary\_intent domina per rischio semantico
```

Esempio:

```text
SL\_HIT + CLOSE\_FULL
```

è più pericoloso da interpretare come comando di chiusura se in realtà è report di stop. Quindi `SL\_HIT` deve dominare.

\---

# Errori da evitare

## Errore 1: classificare con marker singoli

```text
"лонг" trovato -> SIGNAL
```

Sbagliato. `лонг` da solo non basta.

\---

## Errore 2: degradare update senza target a info

```text
стоп в бу
```

Senza reply non diventa `INFO`.

Diventa:

```text
UPDATE + warning update\_without\_target\_hint
```

\---

## Errore 3: confondere report con update

```text
закрылся в бу
```

è report `EXIT\_BE`.

```text
стоп в бу
```

è update `MOVE\_STOP\_TO\_BE`.

La discriminazione deve avvenire con marker strong/weak e regole locali.
