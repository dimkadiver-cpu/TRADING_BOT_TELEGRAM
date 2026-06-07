---
phase: log-templating-system
reviewed: 2026-06-07T00:00:00Z
depth: deep
files_reviewed: 8
files_reviewed_list:
  - src/runtime_v2/control_plane/formatters/_formatters.py
  - src/runtime_v2/control_plane/formatters/_blocks.py
  - src/runtime_v2/control_plane/formatters/templates/__init__.py
  - src/runtime_v2/control_plane/formatters/templates/clean_log.py
  - src/runtime_v2/control_plane/formatters/clean_log.py
  - src/runtime_v2/control_plane/formatters/display.py
  - tests/runtime_v2/control_plane/test_blocks_formatters.py
  - tests/runtime_v2/control_plane/test_clean_log_formatter.py
findings:
  critical: 3
  warning: 6
  info: 4
  total: 13
status: issues_found
---

# Log Templating System: Final Code Review

**Reviewed:** 2026-06-07
**Depth:** deep
**Files Reviewed:** 8
**Status:** issues_found

## Summary

The implementation is well-structured and represents a clean DSL replacement for imperative formatting. The rendering engine in `_blocks.py` is correctly designed, the `_finalize`/`_SEP` sentinel mechanism is sound, and the template decomposition is logical. However, three crash-path bugs were identified that will raise `KeyError` under inputs that are valid at the API level. Six additional warnings cover unguarded `float()` calls in formatters, logic issues in the `or`-chained price lookup, and a sentinel return from `display_symbol(None)` that leaks the string `"None"` into rendered output.

---

## Critical Issues

### CR-01: `KeyError` crash when `fill_price` is absent but `filled_leg_sequence` is present

**File:** `src/runtime_v2/control_plane/formatters/templates/clean_log.py:90-93`

**Issue:** The `_FILL_SECTION` `DerivedBlock` guards the format string with `if p.get("filled_leg_sequence") is not None`, but inside the f-string it accesses `p['fill_price']` with a bare key lookup. Any caller that populates `filled_leg_sequence` without `fill_price` (e.g. a pending order notification or a partial event where price arrives separately) will raise `KeyError: 'fill_price'` at render time.

```python
# Current — crashes if fill_price absent when filled_leg_sequence is set
DerivedBlock(text_fn=lambda p: (
    f"Entry_{p['filled_leg_sequence']}: {num(p['fill_price'])} "
    f"{p.get('entry_type_for_leg', 'Limit').capitalize()}"
    if p.get("filled_leg_sequence") is not None else ""
)),
```

**Fix:**
```python
DerivedBlock(text_fn=lambda p: (
    f"Entry_{p['filled_leg_sequence']}: {num(p.get('fill_price'))} "
    f"{p.get('entry_type_for_leg', 'Limit').capitalize()}"
    if p.get("filled_leg_sequence") is not None else ""
)),
```

---

### CR-02: `KeyError` crash in `_t_update_partial` when `failed_actions` dicts are missing `"action"` or `"reason"` keys

**File:** `src/runtime_v2/control_plane/formatters/templates/clean_log.py:446,451`

**Issue:** `_t_update_partial` constructs `failed_set` and `fn_failed` using bare dict key access on elements of `failed_actions`. If any element lacks the `"action"` or `"reason"` key — which is possible if the upstream operation_rules emits a partial error dict — the transform crashes with `KeyError` before rendering has even started.

```python
# Current — KeyError if dict missing "action" or "reason"
failed_set  = {f["action"] for f in failed_list}          # line 446
...
fn_failed   = [f"Failed: {f['reason']}" for f in failed_list]  # line 451
```

**Fix:**
```python
failed_set  = {f.get("action", "") for f in failed_list}
...
fn_failed   = [f"Failed: {f.get('reason', '?')}" for f in failed_list if f.get("reason")]
```

---

### CR-03: `display_symbol(None)` returns the string `"None"`, not an empty/fallback string

**File:** `src/runtime_v2/control_plane/formatters/display.py:13-14`

**Issue:** The function signature declares `symbol: str | None` but the `if not symbol` branch returns `str(symbol)`. When `symbol=None`, `not None` is `True`, so the function returns `str(None)` = `"None"`. This literal string then appears in rendered output wherever `display_symbol` is called with a missing symbol — e.g. in `_render_chain_item` (`chain.get("symbol", "?")` prevents this there, but `_t_entry_cancelled` calls `display_symbol(p.get("symbol", ""))` with an empty string which is also falsy and returns `str("")=""`, masking the issue in that path while still being semantically incorrect).

The real risk is direct calls to `display_symbol(None)` from other callers in the codebase, and the existing type annotation creates a contract that None is acceptable.

```python
# Current — returns "None" for None input
def display_symbol(symbol: str | None) -> str:
    if not symbol:
        return str(symbol)   # str(None) == "None"
```

**Fix:**
```python
def display_symbol(symbol: str | None) -> str:
    if not symbol:
        return ""   # or "n/a" depending on call-site convention
```

---

## Warnings

### WR-01: `money()`, `pct()`, `pct_signed()`, `fee_rate()`, `money_signed()` crash on non-numeric string values

**File:** `src/runtime_v2/control_plane/formatters/_formatters.py:31-64`

**Issue:** All formatters except `num()` call `float(value)` without a try/except guard. `num()` explicitly wraps `float()` in `try/except (TypeError, ValueError)` and falls back to `str(value)`. The other five formatters do not. If a non-numeric string (e.g. `"n/a"`, `"?"`, a raw value from an upstream dict) reaches any of these formatters, they raise `ValueError` and crash rendering for that notification. This is especially likely for `pct()` and `money_signed()` which are used for `pnl`, `roi_net_pct`, etc. — fields that can come from external exchange data.

```python
# Current — ValueError on non-numeric string
def money(value: object) -> str:
    if value is None:
        return "n/a"
    return f"{float(value):.2f} USDT"
```

**Fix:** Add try/except to each formatter:
```python
def money(value: object) -> str:
    if value is None:
        return "n/a"
    try:
        return f"{float(value):.2f} USDT"
    except (TypeError, ValueError):
        return str(value)
```

Apply the same pattern to `money_signed`, `pct`, `pct_signed`, `fee_rate`.

---

### WR-02: `_t_be_exit` uses `or`-chaining for price lookup, silently drops a zero `sl_price`

**File:** `src/runtime_v2/control_plane/formatters/templates/clean_log.py:202`

**Issue:** `price_value = p.get("sl_price") or p.get("exit_price") or p.get("fill_price")`. The `or` operator short-circuits on truthiness, not on `None`. If `sl_price=0.0` (a zero-value breakeven stop, theoretically valid in certain synthetic hedges), the expression skips it and falls to `exit_price` or `fill_price`. The `price_label` is already set to `"SL"` at line 201 (because `sl_price is not None`), creating a display mismatch: the label says "SL" but the price shown is from a different field.

```python
# Current — 0.0 sl_price is silently skipped
price_value = p.get("sl_price") or p.get("exit_price") or p.get("fill_price")
```

**Fix:**
```python
sl = p.get("sl_price")
price_value = sl if sl is not None else (p.get("exit_price") or p.get("fill_price"))
```

---

### WR-03: Redundant `exit_price` injection in `format_clean_log` dispatcher for the `SL_FILLED → BE_EXIT` path

**File:** `src/runtime_v2/control_plane/formatters/clean_log.py:12-14`

**Issue:** The dispatcher pre-injects `exit_price` into the payload before routing to `BE_EXIT`, and then `_t_be_exit` (the registered transform) recomputes and overwrites `exit_price` anyway. The pre-injection at line 14 is dead work and creates a maintenance trap: future readers may believe the dispatcher's value is authoritative, when it is actually always overwritten. If the dispatcher logic is ever changed without also updating `_t_be_exit`, the two can silently diverge.

```python
# Current — exit_price set twice
payload = {**payload, "exit_price": payload.get("sl_price", payload.get("fill_price"))}  # line 14
# ...then _t_be_exit sets exit_price again at line 202
```

**Fix:** Remove the redundant pre-injection from the dispatcher. The routing to `BE_EXIT` is sufficient; let `_t_be_exit` own all price logic:
```python
def format_clean_log(notification_type: str, payload: dict) -> str:
    if notification_type == "SL_FILLED" and payload.get("close_reason") == "BREAKEVEN_AFTER_TP":
        notification_type = "BE_EXIT"
        # exit_price is handled by _t_be_exit — no pre-injection needed
    config = TEMPLATE_REGISTRY.get(notification_type)
    ...
```

---

### WR-04: `_t_entry_cancelled` uses `display_symbol("")` which produces an empty `_base_asset`, silently rendering `"Total filled: 0.006 "` with trailing space

**File:** `src/runtime_v2/control_plane/formatters/templates/clean_log.py:329-331`

**Issue:** When `symbol` is absent from the payload (`p.get("symbol", "")` returns `""`), `display_symbol("")` returns `""` (via the `not symbol` branch). Then `symbol.split("/")[0] if "/" in symbol else symbol` evaluates to `""`. The `_base_asset` is then an empty string. In `_ENTRY_CANCELLED_BLOCKS`, both the partial-fill line and the total-filled line append `p['_base_asset']`:

```
f"Total filled: {num(p['total_filled_qty'])} {p['_base_asset']}"
```

This renders as `"Total filled: 0.006 "` with a trailing space and no asset name — silently incorrect output.

**Fix:** Use a fallback in `_t_entry_cancelled`:
```python
symbol = display_symbol(p.get("symbol") or "")
base_asset = symbol.split("/")[0] if "/" in symbol else (p.get("symbol") or "?")
```

---

### WR-05: `_FILL_SECTION` partial-leg `DerivedBlock` accesses `p['planned_qty']` with a bare key when `is_partial_leg` is truthy

**File:** `src/runtime_v2/control_plane/formatters/templates/clean_log.py:98-100`

**Issue:** Inside the `BranchBlock` `then_blocks` for partial legs, the DerivedBlock does:
```python
f"Qty: {num(p['filled_qty'])} (planned: {num(p['planned_qty'])})"
```
Both `filled_qty` and `planned_qty` are accessed as bare dict keys. If a partial-leg event arrives with `is_partial_leg=True` but `planned_qty` absent (e.g. the exchange did not return the planned amount), this raises `KeyError`. `filled_qty` is similarly unguarded.

**Fix:**
```python
f"Qty: {num(p.get('filled_qty'))} (planned: {num(p.get('planned_qty'))})"
```

---

### WR-06: `FieldBlock` accepts both `key` and `value_fn` simultaneously with no enforcement — silently ignores `key`

**File:** `src/runtime_v2/control_plane/formatters/_blocks.py:47-53`

**Issue:** The `FieldBlock` docstring says "Use key OR value_fn, not both", but there is no validation. If a caller sets both, `_render_field` silently uses `value_fn` and ignores `key`. This will cause subtle bugs if someone accidentally sets both during template authoring, especially since the field renders without error. At a minimum, a `__post_init__` assertion or a runtime assertion in `_render_field` would catch the mistake during development.

```python
# _render_field
value = block.value_fn(p) if block.value_fn else p.get(block.key)
# No check that both are None or only one is set
```

**Fix:**
```python
@dataclass
class FieldBlock:
    ...
    def __post_init__(self):
        if self.key is not None and self.value_fn is not None:
            raise ValueError(f"FieldBlock '{self.label}': set key OR value_fn, not both")
```

---

## Info

### IN-01: `SectionBlock` is defined and exported but not used in any template

**File:** `src/runtime_v2/control_plane/formatters/_blocks.py:56-60`

**Issue:** `SectionBlock` is defined, included in `__all__`, and appears in the test imports, but is not used in any of the 23 registered templates. It is dead surface area in the public API. Either use it or remove it.

**Fix:** Remove `SectionBlock` from `_blocks.py` and `__all__`, or document it as a reserved building block with a `# noqa: F401` comment to suppress unused-import warnings.

---

### IN-02: `_fmt_counts` omits `"review"` unconditionally from non-`final_close` mode output when count is zero

**File:** `src/runtime_v2/control_plane/formatters/templates/clean_log.py:544-547`

**Issue:** In the `else` branch (non-`final_close` mode), `partial` and `skipped` are always shown (even at 0), but `review` is only shown when non-zero. This inconsistency means the `review` field has different display semantics than other counts. The test at line 562 asserts `"Skipped: 1 | Error: 0"` which confirms `Error: 0` is always shown. `Review: 0` is silently suppressed, making the output format variable.

This is a cosmetic inconsistency, not a crash, but it can confuse readers comparing outputs from different events.

**Fix:** Decide on a consistent rule and apply it uniformly. Either always show all counts (including zeros) or always suppress zeros.

---

### IN-03: `money_signed` and `pct_signed` treat `0` as positive, emitting `"+0.00 USDT"` / `"+0%"`

**File:** `src/runtime_v2/control_plane/formatters/_formatters.py:37-58`

**Issue:** `prefix = "+" if number >= 0 else ""` means zero values render with a `+` prefix. `+0.00 USDT` and `+0%` are technically correct by the "non-negative gets a plus sign" rule, but users reading Telegram notifications may find a `+0` PnL ambiguous (breakeven vs. gain). This is documented by the test `test_money_signed_zero` which explicitly asserts `+0.00 USDT`. If the intended UX is that zero should be sign-neutral, the condition needs changing to `number > 0`.

**Fix (if sign-neutral zero is desired):**
```python
prefix = "+" if number > 0 else ""
```

---

### IN-04: `num()` silently formats numbers using `:.8g` which can produce scientific notation for values outside `[1e-4, 1e15)`

**File:** `src/runtime_v2/control_plane/formatters/_formatters.py:13`

**Issue:** `f"{f:.8g}"` uses general format, which switches to scientific notation for very small values (e.g. `num(0.000001)` → `"1e-06"`). The `"e" not in formatted` check at line 14 falls through to returning the scientific notation string directly. For crypto trading, very small prices (sub-satoshi altcoins) are common, and scientific notation in a Telegram notification is poor UX.

The check `if "e" not in formatted` is a guard, but when it fails (scientific notation is present) the function returns the raw `"1e-06"` string without any formatting transformation.

**Fix:** Use a fixed decimal format for small numbers:
```python
if "e" in formatted:
    # fall back to fixed-point with enough precision
    formatted = f"{f:.8f}".rstrip("0").rstrip(".")
    return formatted
```

---

_Reviewed: 2026-06-07_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: deep_
