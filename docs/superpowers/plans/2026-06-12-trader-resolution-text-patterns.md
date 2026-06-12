# Trader Resolution Text Patterns Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rendere config-driven la risoluzione trader via text patterns nei topic multi-trader, eliminando l'hardcode del topic.

**Architecture:** `channels.yaml` dichiara il `pattern_group` e la `mode` della resolution. `config/text_patterns.yaml` contiene i cataloghi di pattern. `TraderResolver` orchestra alias, text patterns, reply chain e links usando i nuovi campi esposti da `ChannelConfigResolver`.

**Tech Stack:** Python, YAML, pytest, Pydantic models gia' esistenti

---

### Task 1: Contratto config resolver

**Files:**
- Modify: `tests/runtime_v2/test_channel_config_resolver.py`
- Modify: `src/runtime_v2/trader_resolution/channel_config_resolver.py`

- [ ] Scrivere test failing per `resolution.mode` e `pattern_group`
- [ ] Eseguire i test mirati e verificare il fallimento atteso
- [ ] Implementare il parsing minimo dei nuovi campi in `ChannelConfigResolver`
- [ ] Rieseguire i test mirati e verificare il verde

### Task 2: Text pattern matcher

**Files:**
- Modify: `tests/telegram/test_trader_resolver.py`
- Create: `config/text_patterns.yaml`
- Modify: `src/telegram/pattern_extractors.py`
- Modify: `src/telegram/trader_resolver.py`

- [ ] Scrivere test failing per match positivo, ambiguo e `patterns_only`
- [ ] Eseguire i test mirati e verificare il fallimento atteso
- [ ] Implementare loader e matcher config-driven dei text patterns
- [ ] Rieseguire i test mirati e verificare il verde

### Task 3: Config reale e startup validation

**Files:**
- Modify: `config/channels.yaml`
- Modify: `src/startup_check/validator.py`

- [ ] Aggiornare la config reale del topic multi-trader al nuovo contratto
- [ ] Aggiungere la validazione fail-fast del `pattern_group`
- [ ] Eseguire i test/controlli mirati e verificare che il contratto sia coerente

### Task 4: Verifica finale

**Files:**
- Test: `tests/runtime_v2/test_channel_config_resolver.py`
- Test: `tests/telegram/test_trader_resolver.py`

- [ ] Eseguire la suite mirata completa sui file toccati
- [ ] Controllare che non restino riferimenti al gate hardcoded sul topic
- [ ] Riassumere esito, copertura e rischi residui
