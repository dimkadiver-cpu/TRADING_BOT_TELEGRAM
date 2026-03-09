# .codex/NOTES.md

## Local environment da configurare in Codex App

### Setup script suggerito
Windows PowerShell:
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

### Common actions suggerite
- install
  pip install -r requirements.txt

- run-listener
  python -m app.telegram_listener

- run-parser-tests
  pytest tests/parsing -q

- run-lifecycle-tests
  pytest tests/lifecycle -q

- run-backtest
  python scripts/run_backtest.py

- export-stats
  python scripts/export_stats.py

## Nota
Se il repo contiene più progetti, aprire in Codex App la directory specifica del trading bot
che contiene questa cartella .codex condivisa.