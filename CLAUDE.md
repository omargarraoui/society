# Istruzioni per Claude Code

Leggi prima `CONTESTO.md`: contiene l'obiettivo, le decisioni prese (sezione 2), lo stato del codice (sezione 3), le questioni aperte (sezione 4) e la trascrizione completa della chat in cui il progetto è nato.

Regole di lavoro con l'utente:
- Risposte molto brevi e dirette. Spiegazioni in passi piccoli, con numeri concreti.
- Punto "." come separatore decimale.
- Niente modelli in locale e niente costi: solo API gratuite con fallback.
- L'utente non approva nulla a mano: decide l'agente-Dio, l'utente supervisiona via Telegram.
- Verifica sempre i limiti dei piani gratuiti dei provider prima di usarli.
- Per le scelte ancora aperte (sezione 4 di CONTESTO.md), chiedi all'utente invece di decidere.

## Regole d'oro: niente codice slop

Valgono per ogni riga. Codice che non sapresti spiegare riga per riga non entra nel progetto.

1. Commenti e docstring solo per il perché (vincoli, workaround, origine di un numero). Mai ripetere il codice o il nome della funzione.
2. Niente costanti, helper o classi finché non servono in almeno 2 punti. Un valore usato una volta resta inline.
3. Niente astrazioni senza un secondo caso reale: niente interfacce con una sola implementazione, factory per un solo tipo, layer che passano solo la chiamata.
4. Ogni `try/except` deve rispondere a: come può fallire, e cosa vede il chiamante? Se non può fallire, va tolto. Mai `except Exception` generico.
5. Gli errori non si nascondono: niente `None` silenziosi, niente fallback che mascherano un guasto. Il chiamante deve poter distinguere errore da risultato vuoto.
6. Prima di scrivere una funzione, cercare se esiste già nel progetto. Una sola fonte di verità per ogni logica.
7. Un solo modo per ogni cosa: errori, log, accesso al DB, configurazione (i limiti stanno in `config.py`).
8. I bug si correggono dove nascono, non nel chiamante dove si vedono.
9. Test con input e output decisi prima, che verificano il comportamento (non `is not None`, non i mock). Un test nuovo deve fallire senza la correzione. Mai cambiare un valore atteso per far passare un test.
10. Import in cima al file. Ordine: import, costanti, funzioni, classi. File corti, un modulo per argomento.
11. Un commit = una modifica, annullabile da sola.
12. Lint con Ruff, con `BLE001` (except generico) e `PLC0415` (import dentro le funzioni) attivi. Ogni `# noqa` va giustificato nel commento.
13. Dopo ogni modifica: rileggere il diff contro tutto il progetto, togliere prima di aggiungere (guardie morte, costanti usate una volta, wrapper inutili).
