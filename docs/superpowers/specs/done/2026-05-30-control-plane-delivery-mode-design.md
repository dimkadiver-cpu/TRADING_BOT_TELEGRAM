# Control Plane — delivery_mode Extension Design Spec

Date: 2026-05-30
Status: APPROVED
Parent spec: `docs/superpowers/specs/2026-05-29-control-plane-telegram-design.md`

---

## 1. Obiettivo

Aggiungere un campo `delivery_mode` al Control Plane che permette di scegliere tra due modalità operative:

- `supergroup_topics` — supergruppo privato con 3 topic (COMMANDS, TECH_LOG, CLEAN_LOG). Design originale, nessuna modifica al comportamento esistente.
- `private_bot` — chat privata tra operatore e bot. Nessun supergruppo, nessun thread_id. Notifiche e comandi in un'unica chat.

Le due modalità condividono tutta la logica (formatter, outbox, DB, service). Il branching è limitato a config, auth, topic_router, dispatcher e keyboard.

---

## 2. Differenze tra le due modalità

| Aspetto | `supergroup_topics` | `private_bot` |
|---|---|---|
| Chat target | Supergruppo (chat_id negativo) | Chat privata (chat_id = user_id operatore) |
| Thread routing | 3 thread_id distinti | Nessun thread_id (thread_id=None) |
| Auth | chat_id + thread_id + user_id | chat_id + user_id |
| Keyboard | InlineKeyboard (sotto i messaggi) | ReplyKeyboardMarkup (tasti fissi sotto il campo testo) |
| Distinzione CLEAN/TECH | Topic separati | Prefisso testuale su TECH_LOG |
| TECH_LOG prefisso | — | `⚠️ --SYSTEM--\n` |

---

## 3. Config — `telegram_control.yaml`

### `supergroup_topics` (default invariato)

```yaml
delivery_mode: supergroup_topics   # oppure: omesso (default)

token_env: CONTROL_TELEGRAM_BOT_TOKEN
chat_id: "${CONTROL_TELEGRAM_CHAT_ID}"

topics:
  commands:
    thread_id: 101
  tech_log:
    thread_id: 102
  clean_log:
    thread_id: 103

authorized_users:
  - "${CONTROL_TELEGRAM_USER_ID}"
```

### `private_bot`

```yaml
delivery_mode: private_bot

token_env: CONTROL_TELEGRAM_BOT_TOKEN
chat_id: "${CONTROL_TELEGRAM_CHAT_ID}"   # chat privata col bot = user_id operatore

# topics: omessa — thread_id non esiste in chat privata

authorized_users:
  - "${CONTROL_TELEGRAM_USER_ID}"

keyboard:
  - ["/status", "/health", "/control"]
  - ["/trades", "/reviews", "/logs"]
  - ["/pause", "/resume"]
  - ["/block", "/debug_on"]
```

In `private_bot`, `topics` è opzionale. Se omessa, il config loader costruisce `TopicsConfig` con `thread_id=None` su tutti i topic. Se presente ma con `thread_id: null`, si comporta allo stesso modo.

---

## 4. Modifiche a Parte 1 (già implementata)

### 4.1 `models.py`

```python
# ControlPlaneConfig: aggiungere campo delivery_mode
delivery_mode: Literal["supergroup_topics", "private_bot"] = "supergroup_topics"

# TopicConfig: thread_id diventa opzionale
class TopicConfig(BaseModel):
    thread_id: int | None = None

# TechLogConfig e CleanLogConfig: thread_id opzionale (ereditato)
```

### 4.2 `config.py`

La sezione `topics` nel YAML diventa opzionale. Se `delivery_mode=private_bot` e `topics` è assente, il loader inietta un `TopicsConfig` di default con tutti `thread_id=None`:

```python
if raw.get("delivery_mode") == "private_bot" and "topics" not in raw:
    raw["topics"] = {
        "commands": {"thread_id": None},
        "tech_log":  {"thread_id": None},
        "clean_log": {"thread_id": None},
    }
```

Se `delivery_mode=supergroup_topics` e `topics` è assente → `ControlPlaneConfigError` (comportamento invariato).

### 4.3 `auth.py`

```python
def validate(self, *, chat_id: int, thread_id: int | None, user_id: int) -> AuthResult:
    if chat_id != self._chat_id:
        return AuthResult("IGNORE", "wrong_chat")
    if self._delivery_mode == "supergroup_topics":
        if thread_id != self._commands_thread_id:
            return AuthResult("IGNORE", "wrong_topic")
    # private_bot: thread_id ignorato — tutti i messaggi dalla chat autorizzata sono nel topic corretto
    if user_id not in self._authorized:
        return AuthResult("REJECT_UNAUTHORIZED", "unauthorized_user")
    return AuthResult("OK", None)
```

`AuthValidator.__init__` legge `config.delivery_mode` e lo memorizza come `self._delivery_mode`.

**Nota:** i test esistenti di `test_auth.py` restano validi perché usano `delivery_mode=supergroup_topics` (default). Si aggiungono test per `private_bot`.

---

## 5. Impatto sulle parti future

### 5.1 `topic_router.py` (Parte 2)

```python
def route(self, destination: Destination) -> tuple[int, int | None]:
    """Restituisce (chat_id, thread_id). thread_id=None in private_bot."""
    if self._delivery_mode == "private_bot":
        return (self._chat_id, None)
    thread_id = {
        "CLEAN_LOG":      self._cfg.topics.clean_log.thread_id,
        "TECH_LOG":       self._cfg.topics.tech_log.thread_id,
        "COMMANDS_REPLY": self._cfg.topics.commands.thread_id,
    }[destination]
    return (self._chat_id, thread_id)
```

### 5.2 `notification_dispatcher.py` (Parte 2)

Usa il risultato di `topic_router.route()`. Se `thread_id=None`, chiama `send_message` senza il parametro `message_thread_id`:

```python
chat_id, thread_id = self._router.route(entry.destination)
kwargs = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
if thread_id is not None:
    kwargs["message_thread_id"] = thread_id
await bot.send_message(**kwargs)
```

### 5.3 `formatters/tech_log.py` (Parte 5)

In `private_bot`, il formatter prepend il prefisso `⚠️ --SYSTEM--`:

```python
def format_tech_log(entry, delivery_mode: str) -> str:
    body = _build_body(entry)
    if delivery_mode == "private_bot":
        return f"⚠️ --SYSTEM--\n{body}"
    return body
```

### 5.4 `telegram_bot.py` (Parte 3)

In `private_bot`, su `/start` o primo messaggio di un utente autorizzato, il bot invia la Reply Keyboard:

```python
if delivery_mode == "private_bot":
    keyboard = ReplyKeyboardMarkup(
        config.keyboard, resize_keyboard=True, persistent=True
    )
    await update.message.reply_text("Control Plane attivo.", reply_markup=keyboard)
```

In `supergroup_topics` la `keyboard` config viene ignorata per la Reply Keyboard — si usano InlineKeyboard contestuali nei formatter di risposta ai comandi.

---

## 6. Nuovi test da aggiungere a Parte 1

Aggiungere a `test_auth.py`:

```python
def test_private_bot_authorized_no_thread():
    cfg = _config()  # esistente
    cfg = cfg.model_copy(update={"delivery_mode": "private_bot"})
    v = AuthValidator(cfg)
    res = v.validate(chat_id=-100999, thread_id=None, user_id=42)
    assert res.decision == "OK"

def test_private_bot_wrong_chat():
    cfg = _config().model_copy(update={"delivery_mode": "private_bot"})
    v = AuthValidator(cfg)
    res = v.validate(chat_id=-1, thread_id=None, user_id=42)
    assert res.decision == "IGNORE"
    assert res.reason == "wrong_chat"

def test_private_bot_unauthorized_user():
    cfg = _config().model_copy(update={"delivery_mode": "private_bot"})
    v = AuthValidator(cfg)
    res = v.validate(chat_id=-100999, thread_id=None, user_id=99)
    assert res.decision == "REJECT_UNAUTHORIZED"
```

Aggiungere a `test_config.py`:

```python
def test_private_bot_config_without_topics(tmp_path, monkeypatch):
    monkeypatch.setenv("CP_TOKEN", "999:XYZ")
    monkeypatch.setenv("CP_CHAT", "-1009999")
    monkeypatch.setenv("CP_USER", "42")
    yaml_text = """
delivery_mode: private_bot
token_env: CP_TOKEN
chat_id: "${CP_CHAT}"
authorized_users:
  - "${CP_USER}"
"""
    cfg = load_control_plane_config(_write(tmp_path, yaml_text))
    assert cfg.delivery_mode == "private_bot"
    assert cfg.topics.commands.thread_id is None
    assert cfg.topics.tech_log.thread_id is None
    assert cfg.topics.clean_log.thread_id is None
```

---

## 7. Eliminazione di una modalità

Quando si sceglie la modalità definitiva, eliminare l'altra richiede:

1. `grep -r "delivery_mode\|private_bot\|supergroup_topics"` nel package `control_plane/` — identifica tutti i branch
2. Rimuovere i branch if/else corrispondenti
3. `thread_id: int | None` → `thread_id: int` in `TopicConfig` (se si sceglie `supergroup_topics`)
4. Rimuovere il campo `delivery_mode` da `ControlPlaneConfig` e dal YAML
5. Rimuovere i test taggati per la modalità eliminata

Tempo stimato: 30 minuti. Nessuna migrazione DB necessaria.

---

## 8. Acceptance Criteria

```
1. delivery_mode=supergroup_topics si comporta identicamente al design originale — nessuna regressione.
2. delivery_mode=private_bot: auth valida senza thread_id, topic_router ritorna thread_id=None.
3. In private_bot, i messaggi TECH_LOG iniziano con "⚠️ --SYSTEM--".
4. In private_bot, telegram_bot.py invia ReplyKeyboardMarkup su /start.
5. In supergroup_topics, la sezione topics è obbligatoria — config error se assente.
6. In private_bot, la sezione topics è opzionale — default thread_id=None su tutti i topic.
7. test_auth.py e test_config.py coprono entrambe le modalità.
```
