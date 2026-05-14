# listener_sidecar — RIMOSSO (PRD 2.c)

`RuntimeV2ListenerSidecar` e il file `src/runtime_v2/listener_sidecar.py` sono stati eliminati in PRD 2.c.

Il pattern shadow (sidecar affiancato al router legacy) è stato sostituito dalla pipeline runtime_v2 come percorso primario e unico in `TelegramListener._process_item`.

Vedi `docs/runtime_v2/overview.md` per il flusso attuale.
