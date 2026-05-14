

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



Note.

-

- "Applico delle regole di gestione/esecuzione/correzioni vari a seganli update" Operation_rules

Per ogno trader 

Domande:

- Nel caso di ref multipli:
	- come il persocrso downscream sucessico?
    - esecuzione: paralela o in coda?

- Come dovrebbe avvenire il passaggio di esecuzione di un update a un segnale ricevuto in precedenza è aperto?
- Come lo riconosce a quale segante/trade atribuito? 
- A che livello avvienie il controllo se un segnale é eseguibile (policy, se eseite il trade aperto ecc)?
- A che livello avvienie il controllo se il update è eseguibile (policy, se eseite il trade aperto ecc)?
	- policy generale
	- policy trader specifica  
	- Controlla sulla stato della posione
	- Controllo lato exchange 

- Nel caso di target global del trader come fa a sapere i suoi trede? tutti short o tutti long?  

Operation Rules:

- Set di regole di esecuzione dei trade (globali) e singolo trader:
	riguardanti vari livelli di contollo
	


