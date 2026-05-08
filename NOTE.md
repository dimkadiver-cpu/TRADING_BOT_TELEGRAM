
flusso attuale da mantenere:

Telegram -> Parser -> Segnale/Istruzione



- Concetto generale:

	- Ricevo messaggi da telegram
	- parso in segnali /istruzioni operativi 
	- Applico delle regole di gestione/esecuzione
	- Controlli Vari
	- Esecuzione su extange.
		- esecuzione segnali e istruzioni
		- gestione automatica delle posioni in base alle istruzioni


Altri funzionalita acessori:
	- Parser_test: (già presente)
		- Scarico dei dati in DB
		- Test del parser con report
	- dash board ( priferibile, ma non critica se esecutore non presenta)

	- telegram bot: (gia presente o da integrare/fare)
		- Comandi di controllo del bot 
			- blocco esecuzione di nuovo ordini (gestione di quelli gia presenti)
			- chiusura/annulamento manuale di tutti ordini aperti/pendenti	 
		- Statistica 
		- Ordini Pendenti	
		- Trade attiuli  e loro sato storia
		- Notifica di apertura ordini/cycle life



Flusso fino al parser va mantenuto, il resto richiede la revesione.

per esecuzione su extange valuterei Octobot/ nautilustrader / Hummingbot o altri
