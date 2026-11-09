# Limity využití a zablokované fronty

Tvé předplatné Clauda má limity využití: **5hodinové** okno
a **týdenní**. ccc čte tvé skutečné využití — stejná čísla jako
[Analytika](analytics.md) — a plánuje podle nich, takže práce ve frontě
nikdy neselže jen proto, že došel limit.

## Když dojde limit

- Než ccc pošle úkol, zkontroluje využití. Dokud je okno vyčerpané,
  úkol ani nezačne; čeká na obnovení toho okna.
- Pokud Claude narazí na limit uprostřed úkolu, ccc počká na obnovení
  a pak pokračuje. Pokud už úkol ke Claudovi dorazil, ccc místo
  opakování celého úkolu pošle krátkou zprávu *pokračuj* (text je
  v [Nastavení](settings.md)).
- Čekání přežije restart: čas obnovení se ukládá.

Co se stane po obnovení, záleží na přepínači **automatické pokračování**
konverzace (výchozí zapnuto; nastavíš ho při
[zahájení konverzace](new-conversation.md) nebo klávesou `o`
ve [frontě úkolů](tasks.md)):

| Automatické pokračování | Stav | Po obnovení |
|---|---|---|
| zapnuto | `čeká na opakování` `‖` | pokračuje sama |
| vypnuto | `pauza na limitu` `‖` | čeká na tebe: stiskni `r` (nebo pošli zprávu) |

## Rezerva využití

Chceš si část limitu nechat pro sebe? Nastav **rezervu využití**
v [Nastavení](settings.md) (Vypnuto, nebo 50–95 %). S rezervou 80 %
úkoly z fronty nezačnou, dokud je 5hodinové nebo týdenní okno na 80 %
a víc; pokračují po obnovení toho okna. Úkol, který už běží, doběhne.
Analytika ukazuje aktuální nastavení.

## Zablokovaná chybou

Fronta se zastaví — `✖` a stav `zablokovaná chybou` — když úkol selže
ze skutečného důvodu: Claude Code skončil chybou, selhal nástroj, zmizela
relace Claude Code té konverzace. ccc takový úkol sám nikdy nezkouší
znovu, takže se nemůže zacyklit a pálit ti limit.

Jak se to dozvíš:

- `✖` před konverzací v Konverzacích a na Přehledu a červený počet
  *zablokované* v souhrnu Přehledu;
- řádek pod konverzací: *✖ Zablokováno: (chyba) — stiskni r pro nový
  pokus*;
- červené tlačítko **Znovu spustit frontu (r)** vlevo v Konverzacích
  a v detailu fronty;
- zpráva na Telegram / Discord, pokud je máš nastavené
  ([Nastavení](settings.md)).

Co dělat: podívej se na chybu (Konverzace, nebo poslední chyba v detailu
fronty — `Enter` na Přehledu), odstraň příčinu a stiskni `r`.
**`r` znovu spustí úkol, který selhal**, ještě před zbytkem fronty.

Pokud chceš selhaný úkol radši zahodit: pozastav frontu (`t`, `p`),
stiskni `r`, smaž úkol ve frontě (`Delete`) a frontu obnov (`p`).
