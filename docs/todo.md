
flusso attuale da mantenere:

Telegram -> Parser -> Segnale/Istruzione



- Concetto generale:

	- Ricevo messaggi da telegram
	- Parso i dati in segnali /istruzioni operativi strutturati 
	- Applico delle regole di gestione/esecuzione/correzioni vari
	- Controlli Vari (es in caso di Setup se non supera numero massimo del trade, ecc. In caso di update se è possibilie farlo posizione aperta, se suportato dalle regole ecc )
	

	- Esecuzione su extange.
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


Flusso fino al parser va mantenuto, il resto richiede la revesione.

per esecuzione su extange valuterei Octobot/ nautilustrader / Hummingbot o altri



Note.

- Global scopoe si riferisce a posizioni apert dal trader e non tutti ingenerale
