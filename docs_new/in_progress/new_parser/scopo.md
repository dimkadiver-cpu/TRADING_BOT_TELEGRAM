In generale stiamo facendo la riprogettazione del modello dati del parser.
Non stiamo ancora sistemando il parser live pezzo per pezzo.
Stiamo prima definendo la struttura giusta comune su cui poi sistemare tutto il resto.
In pratica
Il problema di partenza è questo:
il Layer 4 parser del progetto è cresciuto in modo un po’ ibrido, quindi oggi ci sono:


parti documentate in un modo


parti usate davvero dal router in un altro


nomi non uniformi


update, report, targeting e dettagli tecnici troppo mescolati


Quindi il lavoro che stiamo facendo è:
1. capire bene il modello attuale
abbiamo analizzato:


cosa salva il parser


come lavora il router


dove ci sono ambiguità


dove c’è ridondanza


dove il contratto non è pulito


2. progettare un nuovo modello canonico
stiamo definendo un Canonical Parser Model v1 che deve essere:


più semplice


universale


uguale per tutti i trader


base per i parser futuri


3. separare bene i concetti
abbiamo deciso di distinguere chiaramente:


SIGNAL


UPDATE


REPORT


INFO


e di ridurre gli update operativi a 5 famiglie canoniche.
4. trasformarlo in specifica e schema
abbiamo già creato:


un file .md con la specifica


una prima bozza .py con schema Pydantic


5. preparare la migrazione futura
solo dopo che il contratto sarà chiuso bene, si farà:


adapter dal parser attuale


adattamento router


migrazione graduale dei parser trader-specifici



Quindi, detto molto semplice
Stiamo costruendo il nuovo “linguaggio comune” del parser.
Cioè:


come deve essere rappresentato un segnale


come deve essere rappresentato un update


come deve essere rappresentato un report


come deve essere rappresentato il target


in modo unico e coerente per tutto il progetto.

Dove siamo ora
Abbiamo già:


chiarito il problema del modello attuale


deciso la struttura del nuovo modello v1


prodotto una specifica markdown


prodotto una prima bozza Pydantic


identificato le differenze da riallineare


Quindi siamo ancora nella fase di progettazione/normalizzazione del contratto, non ancora nella fase di migrazione completa del codice.

In una frase
Stiamo rifondando il contratto canonico del parser, per rendere il Layer 4 più semplice, uniforme e riusabile.
