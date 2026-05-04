# 08 — Multi-ref e targeted actions

## Scopo

Definisce la gestione minima dei messaggi con più riferimenti Telegram nel nuovo parser.

Il parser resta limitato a:

```text
Raw message
↓
ParsedMessage
↓
CanonicalMessage
```

La risoluzione reale del target, la validazione dello stato e l'esecuzione restano fuori parser.

---

## Decisione

Per ora **non introduciamo `InstructionUnit`**.

Motivo: il caso complesso con intenti diversi per ogni link (`link 111 стоп в бу / link 222 закрываю / link 333 лимитки убираем`) non è stato ancora osservato nei dati reali.

Soluzione minima:

```text
CanonicalMessage.update.operations    (single-target message-wide)
CanonicalMessage.targeted_actions     (multi-ref con stessi intent)
```

---

## Casi supportati

### 1. Più link con stesso comando per riga

Forma:

```text
LINK - https://t.me/c/123/978  - стоп в бу
ALGO - https://t.me/c/123/1002 стоп в бу
ARKM - https://t.me/c/123/1003 стоп в бу
```

Interpretazione:

```text
978  -> MOVE_STOP_TO_BE
1002 -> MOVE_STOP_TO_BE
1003 -> MOVE_STOP_TO_BE
```

Output canonical:

```json
{
  "primary_class": "UPDATE",
  "primary_intent": "MOVE_STOP_TO_BE",
  "intents": ["MOVE_STOP_TO_BE"],
  "targeted_actions": [
    {
      "action_type": "SET_STOP",
      "params": {"target_type": "ENTRY"},
      "target_hints": {
        "telegram_message_ids": [978, 1002, 1003],
        "scope_hint": "SINGLE_SIGNAL"
      },
      "source_intent": "MOVE_STOP_TO_BE"
    }
  ]
}
```

Regola: se più righe hanno la stessa firma semantica, raggruppare.

Firma semantica:

```text
action_type + params (escluso target_hints)
```

Esempi:

```text
SET_STOP:target_type=ENTRY
SET_STOP:target_type=TP_LEVEL,value=1
CLOSE:close_scope=FULL
CANCEL_PENDING:cancel_scope_hint=TARGETED
```

### 2. Più link con comando condiviso in coda

Forma:

```text
XRP - https://t.me/c/123/1015
ADA - https://t.me/c/123/1017

А давайте их прикроем
```

Interpretazione:

```text
1015 + 1017 -> CLOSE_FULL
```

Output:

```json
{
  "primary_class": "UPDATE",
  "primary_intent": "CLOSE_FULL",
  "intents": ["CLOSE_FULL"],
  "targeted_actions": [
    {
      "action_type": "CLOSE",
      "params": {"close_scope": "FULL"},
      "target_hints": {
        "telegram_message_ids": [1015, 1017],
        "scope_hint": "SINGLE_SIGNAL"
      },
      "source_intent": "CLOSE_FULL"
    }
  ]
}
```

Marker tipici di "applica ai link sopra":

```text
их
эти сделки
по этим
давайте их
прикроем
```

### 3. Comando globale / selector

Forma:

```text
зафиксировать все шорты
```

Output:

```json
{
  "primary_class": "UPDATE",
  "primary_intent": "CLOSE_FULL",
  "intents": ["CLOSE_FULL"],
  "targeted_actions": [
    {
      "action_type": "CLOSE",
      "params": {"close_scope": "FULL"},
      "target_hints": {"scope_hint": "ALL_SHORT"},
      "source_intent": "CLOSE_FULL"
    }
  ]
}
```

---

## Caso misto NON supportato

Forma:

```text
link 111 стоп в бу
link 222 закрываю
link 333 лимитки убираем
```

Output conservativo:

```json
{
  "primary_class": "UPDATE",
  "parse_status": "PARTIAL",
  "warnings": ["multi_ref_mixed_intents_not_supported"]
}
```

---

## Algoritmo di segmentazione (concreto)

Riferimento: il parser attuale (`src/parser/trader_profiles/common_utils.py:split_lines` + logica per-line in `_build_line_level_move_stop_actions`).

### Definizione "blocco"

Un **blocco** è una **riga non vuota** dopo:

```python
[line.strip() for line in raw_text.splitlines() if line.strip()]
```

Niente raggruppamento per paragrafi (`\n\n`). Una riga = un blocco.

### Algoritmo

```text
INPUT: raw_text, normalized_text, intents (già risolti dal LocalDisambiguator)

1. lines = split_lines(raw_text)

2. per_line_data = []
   for line in lines:
       line_norm = normalize_text(line)
       link_ids  = extract_telegram_message_ids(line)   # vedi target_hints_extractor
       line_intents = match_intents_in_line(line_norm)  # subset di intents che hanno match in questa riga
       per_line_data.append({
           "line": line,
           "link_ids": link_ids,
           "intents": line_intents,
       })

3. # Caso A: ogni riga ha link_ids non vuoti E line_intents non vuoti E
   #         tutte le righe hanno la stessa firma semantica
   if all(item["link_ids"] and item["intents"] for item in per_line_data):
       signatures = {compute_signature(item["intents"]) for item in per_line_data}
       if len(signatures) == 1:
           # raggruppa
           all_ids = [id for item in per_line_data for id in item["link_ids"]]
           emit TargetedAction(
               action_type=...,
               params=...,
               target_hints=TargetHints(telegram_message_ids=dedup(all_ids), scope_hint="SINGLE_SIGNAL"),
               source_intent=primary_intent_of(signatures),
           )
           return
       else:
           # firme diverse per riga → caso misto
           warnings.append("multi_ref_mixed_intents_not_supported")
           parse_status = "PARTIAL"
           return

4. # Caso B: alcune righe hanno solo link, una riga in coda ha l'intent
   link_only_lines = [item for item in per_line_data if item["link_ids"] and not item["intents"]]
   intent_only_lines = [item for item in per_line_data if item["intents"] and not item["link_ids"]]
   if link_only_lines and len(intent_only_lines) == 1 and intent_only_lines[0] is per_line_data[-1]:
       # comando condiviso in coda
       all_ids = [id for item in link_only_lines for id in item["link_ids"]]
       emit TargetedAction(
           action_type=...,
           params=intent_only_lines[0]["intents"][0].params,
           target_hints=TargetHints(telegram_message_ids=dedup(all_ids), scope_hint="SINGLE_SIGNAL"),
           source_intent=...,
       )
       return

5. # Caso C: scope_hint globale (no link)
   if any(scope_hint != "UNKNOWN" for ... in target_hints):
       emit TargetedAction(
           target_hints=TargetHints(scope_hint=...),
           ...
       )
       return

6. # Caso D: link multipli + intenti diversi non riconducibili a un pattern noto
   warnings.append("ambiguous_multi_ref_intent_mapping")
   parse_status = "PARTIAL"
   return

7. # Caso E: nessun link, nessun selector → fallback a update.operations
   emit UpdateOperation per ogni intent (modalità single-target)
```

### Funzioni helper richieste

```python
def split_lines(raw_text: str) -> list[str]:
    return [line.strip() for line in raw_text.splitlines() if line.strip()]

def extract_telegram_message_ids(line: str) -> list[int]:
    # regex t.me/(c/\d+|<channel>)/(\d+) → cattura group "id"
    ...

def compute_signature(intents: list[ParsedIntent]) -> tuple:
    # firma stabile dell'azione (action_type + params hashable)
    ...

def match_intents_in_line(line_norm: str) -> list[ParsedIntent]:
    # subset degli intents globali i cui marker matchano la riga
    ...
```

---

## Contratto `TargetHints`

```python
class TargetHints(BaseModel):
    reply_to_message_id: int | None = None
    telegram_message_ids: list[int] = []
    telegram_links: list[str] = []
    explicit_ids: list[str] = []
    symbols: list[str] = []

    scope_hint: Literal[
        "SINGLE_SIGNAL",
        "SYMBOL",
        "ALL_LONG",
        "ALL_SHORT",
        "ALL_POSITIONS",
        "ALL_OPEN",
        "ALL_REMAINING",
        "UNKNOWN"
    ] = "UNKNOWN"
```

---

## Contratto `TargetedAction`

```python
class TargetedAction(BaseModel):
    action_type: Literal[
        "SET_STOP",
        "CLOSE",
        "CANCEL_PENDING",
        "MODIFY_ENTRIES",
        "MODIFY_TARGETS",
        "INVALIDATE_SETUP"
    ]
    params: dict
    target_hints: TargetHints

    source_intent: IntentType
    raw_fragment: str | None = None
    confidence: float | None = None
```

---

## Relazione con `UpdatePayload`

Per messaggi single-target message-wide:

```text
update.operations
```

Per messaggi multi-ref / selector:

```text
targeted_actions
```

Regola di esclusività:

```text
Se almeno una operation ha target_hints specifici (link/ID per riga), tutto va in targeted_actions.
Altrimenti tutto va in update.operations.
Mai metà in uno, metà nell'altro.
```

---

## Grouping

Il parser raggruppa più azioni con la stessa firma semantica (`action_type + params`).

Esempio:

```text
link 111 стоп в бу
link 222 стоп в бу
```

Output preferito:

```json
{
  "targeted_actions": [
    {
      "action_type": "SET_STOP",
      "params": {"target_type": "ENTRY"},
      "target_hints": {"telegram_message_ids": [111, 222]}
    }
  ]
}
```

Non produrre due azioni separate, salvo necessità di debug.

---

## Regola di sicurezza

Non inventare associazioni ref→intent se non sono chiare.

Se il messaggio contiene:

```text
111
222
333

стоп в бу
закрываю
```

il parser non deve decidere arbitrariamente. Output:

```json
{
  "primary_class": "UPDATE",
  "parse_status": "PARTIAL",
  "warnings": ["ambiguous_multi_ref_intent_mapping"]
}
```

---

## Criterio per introdurre `InstructionUnit`

Solo se nei dati reali compaiono **frequentemente** casi tipo:

```text
link A -> comando X
link B -> comando Y
link C -> comando Z
```

Finché restano rari, `targeted_actions` è sufficiente.

---

## Decisione finale

```text
NO InstructionUnit
SÌ targeted_actions
SÌ TargetHints
SÌ grouping per firma semantica
SÌ warning sui casi multi-ref misti
NO validazione target dentro parser
```
