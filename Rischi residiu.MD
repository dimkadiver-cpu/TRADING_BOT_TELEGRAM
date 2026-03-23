 Rischi aperti

  1. processing_status DEFAULT 'pending' — le righe esistenti nel DB live riceveranno pending dopo la migration, il che farà ri-processare tutto al prossimo restart. Accettabile per ora (DB test), ma
   da gestire prima del deploy live con uno UPDATE raw_messages SET processing_status='done' WHERE ....
  2. channels.yaml è vuoto — finché non si popolano i canali, il listener accetta tutti i chat_id (comportamento intentionally permissivo). Aggiungere canali reali prima del deploy.
  3. Storage layer sync — ingestion.ingest() e ProcessingStatusStore.update() usano sqlite3 sync chiamato dentro worker async. Funziona ma blocca l'event loop per la durata della query (ms).
  Accettabile nella fase attuale.
  4. Test catchup usa patch su Message per bypassare il type check isinstance. Fragile se Telethon cambia. Da rivedere quando si fa integration testing reale.