 Investiga perché SignalBridgeBacktestStrategy genera solo 10 trade su 537 chain.
  Leggi SignalBridgeBacktestStrategy.py e chain_builder.py.
  Controlla populate_entry_trend(): verifica il lookup candele, la logica MARKET vs LIMIT,
  e come viene costruito il signal_chains lookup.
  Poi apri un backtest_reports/run_*/signal_chains.json campione per vedere
  come appaiono le chain reali. Diagnostica e proponi fix.