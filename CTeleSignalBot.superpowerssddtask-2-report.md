# Task 2 Report: Fix SL/BE block nel formatter

**Date:** 2026-06-22  
**Status:** COMPLETE  
**Commit:** `4d86095`

## Summary

Fixed the SL/BE formatting block in `/trade #n` output. The block now always displays (never hidden) and shows the correct price when BE is active, a dash when SL is missing, and "BE: No" only when SL is present but BE is inactive.

## Changes Made

### 1. Added test: `test_trade_detail_sl_missing_shows_dash`
- File: `tests/runtime_v2/control_plane/test_readonly_formatters.py`
- Tests that when `sl_price=None` and `has_be=False`, the SL line shows "SL:    —" with no "BE: No"
- Previously: test did not exist

### 2. Verified existing test: `test_trade_detail_has_be_reflected_in_output`
- File: `tests/runtime_v2/control_plane/test_readonly_formatters.py`
- Tests that when `has_be=True`, the SL line shows "SL:    — · BE: 63,500" (with price, not "BE: set")
- Status: Already present from Task 1, confirmed it works

### 3. Updated SL/BE block logic
- File: `src/runtime_v2/control_plane/formatters/trade_detail.py` (lines 87-96)
- Changed from: `ConditionalBlock` (hidden when `sl_price` is falsy) with logic "BE: set" / "BE: No"
- Changed to: `DerivedBlock` (always visible) with three-way logic:
  - If `has_be=True`: `"SL:    — · BE: {sl_price}"`
  - Else if `sl_price` exists: `"SL:    {sl_price} · BE: No"`
  - Else: `"SL:    —"`

## Validation

**Primary Signal:** All tests pass  
**Test Results:** 23/23 passed in `test_readonly_formatters.py`

Two target tests now passing:
- ✅ `test_trade_detail_has_be_reflected_in_output` — BE active shows price with dash
- ✅ `test_trade_detail_sl_missing_shows_dash` — SL missing shows dash, no BE: No

No regressions detected. All 23 existing formatter tests continue to pass.

## Technical Details

**Root cause solved:** The old conditional wrapper hid the SL line entirely when `sl_price` was None, violating the spec that SL should always be visible (as "—" when missing). Additionally, "BE: set" was not the correct output—when BE is active, the actual SL price is moved to the BE column and SL shows "—".

**Affected layers:**
- Formatter presentation layer only
- No database, schema, or API changes
- No impact on upstream data flow

## Risks & Notes

- None identified. SL line is now always rendered, which matches the UX spec.
- The change assumes `has_be` and `sl_price` fields are correctly populated upstream (verified working by Task 1).

---

**Commit message:**  
```
fix: SL/BE block shows price when BE active, dash when SL absent
```

**Files changed:**
- `src/runtime_v2/control_plane/formatters/trade_detail.py` — formatter logic (9 lines changed)
- `tests/runtime_v2/control_plane/test_readonly_formatters.py` — added 1 test (8 lines added)
