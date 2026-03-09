# TRADER A PROFILE

## 1. Identity

**Canonical trader id**
- `TA`

**Supported trader tags**
- `trader#a`

Equivalent forms are accepted after normalization, for example:
- `[trader#A]`
- `[trader #A]`
- `[TRADER#A]`
- `[trader#a]`

### Resolution rules
Priority:
1. direct trader tag in message
2. inherit from replied parent message
3. source-only fallback only if explicitly configured as safe mono-trader source

---

## 2. Message families used by Trader A

Trader A produces at least these categories:

### `NEW_SIGNAL`
Structured new setup with symbol, direction, entries, stop, and targets.

### `UPDATE`
Operational update on an existing setup/trade, such as:
- target hit
- move stop to breakeven
- remove pending limit order
- close position
- multi-action status update

### `INFO_ONLY`
Narrative or non-operational status/commentary.

### `SETUP_INCOMPLETE`
Possible when setup intent exists but required fields are missing.

---

## 3. New signal vocabulary

### Direction
Observed:
- `лонг` -> `LONG`

Expected:
- `шорт` -> `SHORT`

### Symbol
Strong pattern:
- `#...USDT`

Examples:
- `#ASTERUSDT`
- `#LDOUSDT`
- `#TIAUSDT`

### Entry
Observed forms:
- `вход`
- `вход с текущих`
- `вход лимиткой`
- `усреднение`
- `вход (2-фазный)`

Interpretation:
- Trader A may use multi-step entries:
  - primary entry
  - secondary averaging entry

### Stop
Observed forms:
- `стоп`
- `sl`

### Targets
Observed forms:
- `tp1`
- `tp2`
- `tp3`
- additional TP levels possible

### Risk hint
Observed form:
- `риск на сделку ... %`

Rule:
- collect as message hint only
- do not apply directly

### Conditional entry invalidation
Observed forms:
- `отмена входа`
- conditions based on 15m close
- conditions above/below certain level

Rule:
- store as raw invalidation note
- do not treat as immediate update

Suggested raw field:
- `entry_cancel_rule_raw`

---

## 4. New signal classification rules

A Trader A message tends to be `NEW_SIGNAL` when it contains:

- trader resolved
- symbol
- direction
- entry
- stop
- at least one target

Strong signal indicators:
- `#...USDT`
- `лонг` / `шорт`
- `вход`
- `стоп` / `sl`
- `tp1`

If setup intent is present but required fields are incomplete:
- classify as `SETUP_INCOMPLETE`

---

## 5. Update vocabulary

### Target hit / profit event
Observed or expected forms:
- `тейк`
- `тейкнулось`
- `tp1`
- `tp2`
- similar take-profit event wording

Type:
- `UPDATE`

### Breakeven / stop moved
Observed forms:
- `стоп в бу`
- `перевел стоп в бу`
- `breakeven`

Type:
- `UPDATE`

### Remove pending entry / cancel limit
Observed forms:
- `убрать лимитку`
- `удалить лимитку`

Type:
- `UPDATE`

### Close / close all / 100% fix
Observed forms:
- `закрываю`
- `фиксирую 100%`
- explicit full close wording

Type:
- `UPDATE`

### Multi-action update
Trader A may send a single message containing:
- TP hit
- breakeven move
- pending order removal
- close actions

Rule v1:
- classify as `UPDATE`
- preserve full raw text
- do not attempt full multi-action decomposition yet unless parser explicitly supports it

---

## 6. Info-only rules

Messages should be `INFO_ONLY` when they:
- describe status
- provide commentary
- narrate waiting behavior
- do not contain complete setup
- do not contain operational modification
- do not contain strong linkage to an existing setup/trade

Examples:
- waiting comments
- narrative progress updates
- general market commentary
- non-operational status notes

---

## 7. Linkage behavior

Trader A uses multiple linkage styles.

### Strong linkage methods
1. `REPLY`
2. `MESSAGE_LINK`
3. `EXPLICIT_MESSAGE_ID`

### Weak linkage
- pure context without strong reference

Rule:
- do not auto-apply short or ambiguous updates without strong linkage

### Multi-trade update messages
Trader A may reference:
- multiple links
- multiple symbols
- multiple actions in one update

Rule v1:
- save as `UPDATE`
- do not auto-apply aggressively if system does not yet support multi-target split

---

## 8. Special rules

### Rule A
`тейк` alone is an update event, not automatically a full close.

### Rule B
`стоп в бу` is a strong operational update, but still requires linkage.

### Rule C
Messages containing multiple symbols and multiple actions are not simple single-target updates.
For v1:
- classify as `UPDATE`
- preserve
- avoid unsafe auto-application

### Rule D
Entry invalidation conditions belong to the setup definition, not to immediate trade updates.

---

## 9. Suggested config blocks for Trader A

### `signal_keywords`
- `лонг`
- `шорт`
- `вход`
- `усреднение`
- `стоп`
- `sl`
- `tp1`
- `tp2`
- `tp3`

### `update_keywords`
- `тейк`
- `бу`
- `breakeven`
- `убрать лимитку`
- `закрываю`
- `фиксирую`
- `стоп в бу`

### `info_only_patterns`
- waiting comments
- general commentary
- non-operational progress text

### `linkage_rules`
Priority:
1. `REPLY`
2. `MESSAGE_LINK`
3. `EXPLICIT_MESSAGE_ID`
4. weak context only in later advanced phase

---

## 10. Known limitations of v1

Not fully covered yet:
- automatic decomposition of multi-action updates
- automatic split of multi-symbol updates
- full semantic handling of conditional invalidation rules
- precise action decomposition for complex close/management messages

---

## 11. Summary

Trader A is:
- structured enough for a strong trader-specific parsing profile
- a good first candidate for real trader-profile implementation

Strong points:
- recurring signal structure
- clear update vocabulary
- usable reply/linkage styles
- readable symbol / stop / TP patterns

Delicate points:
- multi-entry structure
- multi-action updates
- multi-symbol update messages
- setup invalidation conditions

---

## Rule summary for docs

> Trader A is a relatively structured trader profile with strong recurring patterns for new signals and clear update vocabulary.  
> It supports trader-tag-based identity, reply and message-link linkage, multi-step entries, TP-based progress updates, breakeven updates, and conditional entry invalidation notes.  
> Multi-symbol and multi-action updates should be preserved as `UPDATE` in v1, but not aggressively auto-applied unless explicit multi-target support exists.
