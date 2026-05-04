# 06_1 — Semantic markers (file completo)

Questo è il contenuto canonico di `semantic_markers.json` per il profilo Trader A.

> ⚠️ **Nota**: questo blocco è JSON valido, copiabile direttamente. Non escapare gli underscore nei nomi degli intent — devono restare `MOVE_STOP_TO_BE`, non `MOVE\_STOP\_TO\_BE`.

> ⚠️ **`number_format` è solo un hint diagnostico**. Il `Price` parser deve essere robusto e gestire tutti i formati osservati (`1234`, `1234.56`, `1 234,56`, `90 000.5`, `90,000.5`, `0.1772`) senza dipendere da questa configurazione. Vedi [10_COSE_UTILI...md](10_COSE_UTILI_DA_RECUPERARE_DAL_PARSER_ATTUALE.md) §2 per il contratto `Price`.

```json
{
  "language": "ru",
  "number_format_hint": {
    "primary_decimal_separator": ".",
    "primary_thousands_separator": " ",
    "note": "diagnostic only — Price parser must accept multiple formats"
  },
  "field_markers": {
    "symbol": {
      "strong": [],
      "weak": []
    },
    "entry": {
      "strong": [],
      "weak": []
    },
    "stop_loss": {
      "strong": [],
      "weak": [
        "сл",
        "стоп"
      ]
    },
    "take_profit": {
      "strong": [],
      "weak": []
    },
    "risk": {
      "strong": [
        "риск",
        "риск на сделку",
        "от депозита",
        "risk"
      ],
      "weak": [
        "%"
      ]
    },
    "leverage": {
      "strong": [
        "плечо",
        "leverage"
      ],
      "weak": [
        "x"
      ]
    }
  },
  "side_markers": {
    "LONG": {
      "strong": [
        "long",
        "лонг"
      ],
      "weak": [
        "buy"
      ]
    },
    "SHORT": {
      "strong": [
        "short",
        "шорт"
      ],
      "weak": [
        "sell"
      ]
    }
  },
  "entry_type_markers": {
    "MARKET": {
      "strong": [
        "вход с текущих",
        "вход по рынку",
        "с текущих",
        "по текущим",
        "по рынку",
        "market",
        "market entry",
        "enter market",
        "market now"
      ],
      "weak": [
        "текущих",
        "рынку"
      ]
    },
    "LIMIT": {
      "strong": [
        "лимит",
        "лимитка",
        "лимиткой",
        "лимитным ордером",
        "limit",
        "limit entry",
        "limit order"
      ],
      "weak": []
    }
  },
  "intent_markers": {
    "MOVE_STOP_TO_BE": {
      "strong": [
        "стоп в бу",
        "стопы в бу",
        "стоп в безубыток",
        "стоп в безубытке",
        "стоп на точку входа",
        "стоп на вход",
        "стоп на цену входа",
        "стоп в ноль",
        "переводим в бу",
        "перевод в бу",
        "переводим в безубыток",
        "перевод в безубыток",
        "перевести стоп в безубыток",
        "стоп переводим в бу",
        "стоп переводим в безубыток",
        "перенести стоп в бу",
        "переносим стоп в бу",
        "move stop to be",
        "stop to breakeven",
        "stop to entry",
        "sl to be",
        "sl to entry"
      ],
      "weak": [
        "бу",
        "безубыток",
        "breakeven",
        "be",
        "на вход"
      ]
    },
    "MOVE_STOP": {
      "strong": [
        "стоп на 1 тейк",
        "стоп на первый тейк",
        "стоп на tp1",
        "стоп на 2 тейк",
        "стоп на второй тейк",
        "стоп на tp2",
        "стоп переносим",
        "переносим стоп",
        "перенос стопа",
        "переставить стоп",
        "переставляем стоп",
        "стоп переставляем",
        "сдвигаем стоп",
        "поднять стоп",
        "новый стоп",
        "стоп поставьте на",
        "стоп переношу на",
        "move stop",
        "move sl",
        "update stop",
        "new sl"
      ],
      "weak": [
        "стоп на",
        "sl на",
        "stop на"
      ]
    },
    "CLOSE_FULL": {
      "strong": [
        "закрываю по текущим",
        "закрываю по текущей",
        "закрываю на текущих",
        "закрываю все позиции",
        "закрываю полностью",
        "закрываем полностью",
        "закрываем позицию полностью",
        "закрываю сделку",
        "закрываем сделку",
        "закрываю по рынку",
        "зафиксировать все позиции",
        "зафиксировать все шорты",
        "зафиксировать все лонги",
        "фиксирую все позиции",
        "фиксирую все",
        "фиксация 100%",
        "фиксация 100% по текущим",
        "фиксация 100% по текущим отметкам",
        "давайте все закроем",
        "давайте закроем",
        "прикроем",
        "прикроем ее",
        "close full",
        "close all",
        "close position",
        "exit all"
      ],
      "weak": [
        "закрываю",
        "закрываем",
        "зафиксировать",
        "фиксирую",
        "закрыть"
      ]
    },
    "CLOSE_PARTIAL": {
      "strong": [
        "фикс 50%",
        "фиксирую 50%",
        "фиксируем 50%",
        "срезаем 50%",
        "срезал 50%",
        "срезал еще 25%",
        "частично закрываем",
        "частичная фиксация",
        "закрыть 80%",
        "закрывать 80%",
        "забираем часть",
        "сокращаем позицию",
        "partial close",
        "close partial",
        "close half",
        "take partial"
      ],
      "weak": [
        "частично",
        "половину",
        "часть"
      ]
    },
    "CANCEL_PENDING": {
      "strong": [
        "убираем лимитки",
        "уберем лимитки",
        "отменяем лимитки",
        "снять лимитки",
        "снимаем лимитки",
        "снять все лимитные ордера",
        "снимаем лимитные ордера",
        "убрать все лимитные ордера",
        "убрать лимитку",
        "лимитку убираем",
        "лимитку на усреднение убираем",
        "отменяем лимитку",
        "отменить лимитки",
        "отменить лимитный ордер",
        "cancel pending",
        "cancel limit",
        "cancel limits",
        "cancel orders",
        "remove pending",
        "remove limit"
      ],
      "weak": [
        "убираем",
        "отменяем",
        "снимаем",
        "cancel"
      ]
    },
    "INVALIDATE_SETUP": {
      "strong": [
        "тут отмена",
        "отмена входа",
        "отмена сетапа",
        "сетап отменен",
        "сигнал отменен",
        "не актуально",
        "пока не актуально",
        "отбой",
        "тоже отбой",
        "старый сигнал закрыт",
        "setup invalid",
        "invalidate setup",
        "cancel setup"
      ],
      "weak": [
        "отмена",
        "отбой"
      ]
    },
    "MODIFY_ENTRY": {
      "strong": [
        "меняем вход",
        "обновляем вход",
        "новый вход",
        "вход теперь",
        "вход сейчас",
        "актуальный вход",
        "новая цена входа",
        "переводим вход в рынок",
        "убираем вход",
        "убрать вход",
        "удаляем вход",
        "удалить вход",
        "вход не актуален",
        "старый вход не актуален",
        "entry update",
        "modify entry",
        "update entry",
        "remove entry",
        "delete entry"
      ],
      "weak": [
        "новый вход"
      ]
    },
    "ADD_ENTRY": {
      "strong": [
        "добавляю вход",
        "добавляем вход",
        "добавляю лимитку",
        "добавляем лимитку",
        "добавить вход",
        "добавить лимитку",
        "добавляю усреднение",
        "добавляем усреднение",
        "add entry",
        "add limit",
        "add position",
        "averaging in"
      ],
      "weak": [
        "добавляю",
        "добавляем",
        "усреднение"
      ]
    },
    "REENTER": {
      "strong": [
        "перезайдем",
        "перезаходим",
        "заходим заново",
        "входим заново",
        "повторный вход",
        "reenter",
        "re-entry",
        "enter again"
      ],
      "weak": [
        "заново"
      ]
    },
    "MODIFY_TARGETS": {
      "strong": [
        "обновить тейки",
        "тейки обновить",
        "обновляем тейки",
        "новые тейки",
        "новые цели",
        "убираю 2 и 3 тейк",
        "первый тейк убираем",
        "тейк убираем",
        "добавлю тейк",
        "добавляем тейк",
        "modify targets",
        "update targets",
        "update take profits",
        "new targets",
        "new tp",
        "remove tp"
      ],
      "weak": [
        "новые цели",
        "новые тейки"
      ]
    },
    "ENTRY_FILLED": {
      "strong": [
        "вход исполнен",
        "ордер исполнен",
        "исполнилась",
        "лимитка взялась",
        "взяли лимитку",
        "взяло лимитку",
        "усреднение взято",
        "взяли усреднение",
        "моя средняя",
        "entry filled",
        "order filled",
        "limit filled",
        "filled"
      ],
      "weak": [
        "исполнен",
        "исполнилась",
        "средняя",
        "лимитка"
      ]
    },
    "TP_HIT": {
      "strong": [
        "тейк взят",
        "тейк взяли",
        "взяли тейк",
        "первый тейк",
        "1 тейк",
        "второй тейк",
        "2 тейк",
        "2 тейка",
        "третий тейк",
        "3 тейк",
        "все тейки",
        "дошли до 2-х тейков",
        "дошли до тейка",
        "цель достигнута",
        "цели достигнуты",
        "tp hit",
        "tp1 hit",
        "target hit",
        "take profit hit"
      ],
      "weak": [
        "tp1",
        "tp 1",
        "tp2",
        "tp 2",
        "tp3",
        "tp 3",
        "тейк",
        "цель"
      ]
    },
    "SL_HIT": {
      "strong": [
        "выбило по стопу",
        "выбило стоп",
        "сработал стоп",
        "стоп сработал",
        "словили стоп",
        "к сожалению стоп",
        "увы стоп",
        "обидный стоп",
        "тут был стоп",
        "стоп был",
        "закрылись по стопу",
        "закрылись по стоп лоссу",
        "stop hit",
        "sl hit",
        "stopped out",
        "hit stop"
      ],
      "weak": [
        "стоп",
        "stop",
        "sl"
      ]
    },
    "EXIT_BE": {
      "strong": [
        "ушел в бу",
        "ушла в бу",
        "позиция ушла в бу",
        "остаток ушел в бу",
        "остаток позиции ушел в бу",
        "сделка закрылась в безубыток",
        "закрылось в безубыток",
        "закрылся в бу",
        "закрылась в бу",
        "закрыта в бу",
        "закрылись в бу",
        "также в бу закрылись",
        "в безубыток закрылся",
        "closed at breakeven",
        "exit be",
        "be exit",
        "stopped at be"
      ],
      "weak": [
        "бу",
        "безубыток",
        "breakeven",
        "be"
      ]
    },
    "REPORT_RESULT": {
      "strong": [
        "итог",
        "итого",
        "результат",
        "результаты",
        "общий профит",
        "профит по сделке",
        "сделка закрыта",
        "позиция закрыта",
        "сетап полностью закрыт",
        "все тейки, сделка закрыта",
        "поздравляю",
        "прибыль",
        "убыток",
        "профит",
        "чистыми",
        "чистого движения",
        "заработали",
        "final result",
        "result",
        "results",
        "trade result"
      ],
      "weak": [
        "профит",
        "убыток",
        "закрыта",
        "закрыт"
      ]
    }
  },
  "modify_entry_mode_markers": {
    "MARKET_NOW": {
      "strong": [
        "входим по рынку",
        "заходим по рынку",
        "вход по рынку",
        "входим с текущих",
        "заходим с текущих",
        "вход с текущих",
        "можно входить по рынку",
        "можно заходить по рынку",
        "можно заходить с текущих",
        "по текущим можно входить",
        "по рынку можно входить",
        "market now",
        "enter market now",
        "enter at market",
        "market entry now",
        "enter now"
      ],
      "weak": [
        "по рынку",
        "с текущих",
        "market"
      ]
    },
    "UPDATE_PRICE": {
      "strong": [
        "новый вход",
        "вход теперь",
        "вход сейчас",
        "обновляем вход",
        "актуальный вход",
        "новая цена входа",
        "entry update",
        "update entry",
        "new entry",
        "new entry price"
      ],
      "weak": [
        "новый",
        "теперь",
        "сейчас",
        "актуальный"
      ]
    },
    "REMOVE": {
      "strong": [
        "убираем вход",
        "убрать вход",
        "удаляем вход",
        "удалить вход",
        "вход не актуален",
        "старый вход не актуален",
        "entry removed",
        "remove entry",
        "delete entry"
      ],
      "weak": [
        "убираем",
        "удаляем",
        "не актуален"
      ]
    }
  },
  "info_markers": {
    "INFO": {
      "strong": [
        "#admin",
        "это админ",
        "админ на связи",
        "обзор рынка",
        "ситуация на рынке",
        "market overview",
        "не является индивидуальной инвестиционной рекомендацией",
        "не иир",
        "не финансовый совет"
      ],
      "weak": [
        "привет",
        "начинаем",
        "обзор",
        "рынок"
      ]
    }
  },
  "target_hint_markers": {
    "telegram_link": {
      "strong": [
        "t.me/"
      ],
      "weak": []
    },
    "explicit_id": {
      "strong": [
        "signal id",
        "signal id:",
        "сигнал id",
        "id сигнала"
      ],
      "weak": [
        "id"
      ]
    },
    "symbol": {
      "strong": [
        "usdt",
        "usdc",
        ".p"
      ],
      "weak": []
    },
    "ALL_LONG": {
      "strong": [
        "все лонги",
        "все мои лонги",
        "по всем лонгам",
        "всем лонгам",
        "моим лонгам"
      ],
      "weak": [
        "лонги"
      ]
    },
    "ALL_SHORT": {
      "strong": [
        "все шорты",
        "все мои шорты",
        "по всем шортам",
        "всем шортам",
        "моим шортам",
        "по шортам"
      ],
      "weak": [
        "шорты"
      ]
    },
    "ALL_POSITIONS": {
      "strong": [
        "все позиции",
        "все мои позиции",
        "все свои позиции",
        "по всем позициям",
        "всем позициям",
        "все сделки",
        "все мои сделки"
      ],
      "weak": [
        "позиции",
        "сделки"
      ]
    },
    "ALL_OPEN": {
      "strong": [
        "все открытые позиции",
        "все открытые сделки",
        "все активные сделки"
      ],
      "weak": []
    },
    "ALL_REMAINING": {
      "strong": [
        "все оставшиеся позиции",
        "все оставшиеся сделки",
        "остаток позиций",
        "остаток позиции"
      ],
      "weak": [
        "остаток"
      ]
    }
  },
  "ignore_markers": [
    "#admin",
    "# админ",
    "это админ",
    "админ на связи",
    "старт:",
    "финиш:",
    "не является индивидуальной инвестиционной рекомендацией",
    "не иир",
    "не финансовый совет",
    "друзья, привет",
    "всем привет"
  ],
  "blacklist": []
}
```

---

## Note importanti

### Marker collidenti `entry_type_markers.MARKET` ↔ `modify_entry_mode_markers.MARKET_NOW`

Entrambe le sezioni contengono marker simili (`"вход по рынку"`, `"с текущих"`, `"по рынку"`).

La regola di disambiguazione è **contestuale**:

```text
- Se nel messaggio è stata estratta una struttura signal (symbol + side + ... presenti):
  → il marker è interpretato come entry_type=MARKET dentro SignalDraft
- Altrimenti:
  → il marker è interpretato come MODIFY_ENTRY/mode=MARKET_NOW
```

Questa regola va in `rules.json` (vedi [06_MARKERS_RULES.md](06_MARKERS_RULES.md)) come `disambiguation` con condizione `if_signal_payload_present`.

### `info_markers` semplificato

Il vecchio schema aveva sottocategorie (ADMIN, SCHEDULE, GREETING, DISCLAIMER, MARKET_COMMENT). Il nuovo parser **non le distingue**: il `InfoPayload` conserva solo `raw_fragment`. I marker informativi vengono aggregati in un'unica chiave `INFO`.

### `ignore_markers`

Lista di stringhe che, se presenti, non producono intent ma vengono comunque registrate in diagnostics. Sono usate per evitare falsi positivi sui weak marker (es. `#admin` non deve far classificare come UPDATE).
