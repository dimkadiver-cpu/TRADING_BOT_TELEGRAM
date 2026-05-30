---
created: 2026-05-30T19:01:00Z
title: Fix control_bot.run() task-leak on startup failure
area: general
files:
  - main.py:446
---

## Problem

In `_async_main()`, `await control_bot.run()` is called bare inside the `try` block (line ~446), after `worker_task` and `lifecycle_task` have already been created. If `run()` raises (e.g., Telegram returns a 401 or network error on `getMe`), execution jumps to the outer `finally` (`await client.disconnect()`) without entering the inner `finally` that cancels those tasks. The tasks are leaked in the event loop.

This was flagged as an Important issue during the 2026-05-30 spec-gap closure code review (Task 4) but deliberately not fixed because it is pre-existing and requires a non-trivial refactor.

## Solution

Option A: Wrap `await control_bot.run()` in `try/except` and, on failure, cancel already-created tasks before re-raising.

Option B: Create all tasks (worker, lifecycle, dispatcher) first, then start the bot, then `await client.run_until_disconnected()` — following the pattern used for sync workers. This keeps the task lifecycle consistent and the inner `finally` always reachable.

Option B is cleaner. Implement after smoke-testing the current runtime in production to confirm the pre-existing risk is acceptable short-term.
