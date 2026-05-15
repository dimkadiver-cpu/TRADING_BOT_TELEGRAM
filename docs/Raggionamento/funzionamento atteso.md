

- Concetto generale:


	- Ricevo messaggi da telegram
	- Parso i dati in segnali /istruzioni operativi strutturati 
	- Applico delle regole di gestione/esecuzione/correzioni vari a seganli update 
	- Controlli Vari (es in caso di Setup se non supera numero massimo del trade, ecc. In caso di update se è possibilie farlo posizione aperta, se suportato dalle regole ecc )
	
	- Esecuzione di update/segnali (validi eseguibili) su extange.
		- esecuzione segnali e istruzioni
		- gestione automatica delle posioni:
				- in base alle istruzioni (es evento su Exchange TP2 HIT -> spostamento del SL a BE )
				- aggiornamento dello stato in bd

Altri funzionalita acessori:
	- Parser_test: (già presente) scopo produrre validare i parser 
		- Scarico dei dati in DB
		- Test del parser con report

	- dashboard (priferibile, ma non critica se esecutore non presenta)

	- telegram bot: (gia presente o da integrare/fare)
		- Comandi di controllo del bot 
			- blocco esecuzione di nuovo ordini (gestione di quelli gia presenti)
			- chiusura/annulamento manuale di tutti ordini aperti/pendenti	 
		- Statistica 
		- Ordini Pendenti	
		- Trade attiuli  e loro sato storia
		- Notifica di apertura ordini/cycle life

	-  possibilità di collegare anche una strategia, indicatori, ecc , che fungano come layer di controllo e aggiustamento dei dati (es st loss, tp )




Per esecuzione su extange  Hummingbot o altri


Cycle_life funzionalita attese:

- Segale:
	- Verifica eseguibilita
		- numero di posizioni aperti/rischio amesso/ se symbol è supportato ecc
		- Calcola quantitativo della posizione
		- manda ordine al exctange



