# Agenti

Agent (Claude Code mu říká *subagent*) je **pomocník s jedním úkolem**:
„spusť testy a nahlas jen výsledek“, „najdi, kde se tahle funkce
používá“, „zkontroluj diff“. Claude mu zadá úkol, pomocník pracuje ve
vlastním kontextu a Claude dostane zpět jen shrnutí. Konverzace tak zůstane
krátká a spotřeba nízká.

Klávesa `A` kdekoli otevře **Agenty**: pomocníky, které Claude Code
najde pro vybranou konverzaci.

| Klávesa | |
|---|---|
| `↑` / `↓` | vybere agenta; pod seznamem vidíš jeho soubor, nástroje a začátek promptu |
| `Esc` | zavře okno |

## Kde agenti jsou

Každý agent je soubor Markdown:

- **tento projekt**: `.claude/agents/<jméno>.md` ve složce konverzace
  (nebo v nadřazené až po kořen repozitáře). Dej ho do gitu a pomocníka
  má celý tým.
- **všechny projekty**: `~/.claude/agents/<jméno>.md`.

Mají-li oba stejné jméno, vyhrává agent projektu; druhý je ztlumený
(„nepoužije se“).

## Soubor, který Claude Code přeskočí

Rozbitý soubor agenta Claude Code přeskočí **bez upozornění**. Agenti ho
ukážou červeně i s důvodem, třeba *chybí popis* (podle popisu Claude
rozhoduje, kdy agenta zavolat) nebo jméno, které začíná `-` nebo obsahuje
`:`.

## Dobrý agent

- **Jeden úkol**, řečený v popisu i s tím, *kdy* ho použít („Použij po
  změnách kódu“). Vágní popis znamená, že ho Claude nikdy nezavolá.
- **Co nejméně nástrojů**: revizor nebo průzkumník jen čte
  (`Read, Grep, Glob`).
- **Levnější model** (`haiku`, `sonnet`), pokud to úkol dovolí.

Agenty volá Claude sám. Chceš-li mít jistotu, že běží právě ten,
pojmenuj ho ve zprávě.
