# Promemoria — `parser_v2`: marker globali regex e precedenza

## Contesto

Si sta valutando di introdurre in `parser_v2` nuovi marker globali basati su regex, mantenendo il modello attuale `strong` / `weak` e aggiungendo due campi distinti:

- `strong_patterns`
- `weak_patterns`

L'obiettivo è permettere al matcher di riconoscere pattern espressi come regex senza spostare questa logica nelle sole rules di soppressione.

## Punto importante da non dimenticare

Le `rules` attuali gestiscono già parte della risoluzione dei conflitti:

- `suppress_weak_inside_strong_same_intent`
- `weak_context_exclusions`
- `marker_context_exclusions`
- `cross_intent_suppression`

Quindi il sistema **non è privo di precedenza**.

Il limite è che queste rules operano **dopo** il matching:

1. il matcher trova tutti gli span
2. le rules sopprimono o mantengono i match

Questo significa che, con regex globali, resta necessario definire una **policy di precedence nel matcher** per i casi di overlap o match annidati.

## Rischio da tenere presente

Se si aggiungono `strong_patterns` / `weak_patterns` senza una precedenza esplicita, possono comparire casi come:

- regex `strong` che matcha dentro un `weak`
- regex e literal che matchano lo stesso span con priorità diversa
- overlap tra intent diversi che oggi non è coperto da una rule specifica

Le rules riducono il problema, ma non lo eliminano in modo generale.

## Nota di progettazione

Per questa estensione conviene trattare i regex marker come marker di prima classe nel matcher, non come un canale parallelo "speciale".

Decisione da prendere prima dell'implementazione:

- ordine di precedenza tra literal e regex
- regola di tie-break sugli overlap
- diagnostica per capire se un match è nato da literal o da pattern

## Scope futuro

Lo scope 2 resta separato:

- usare regex anche nelle rules di soppressione / contesto (`if_regex_any`)

Questo promemoria riguarda solo il punto 1: **nuovi marker globali regex nel matcher**.

