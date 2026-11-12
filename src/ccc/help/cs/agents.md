# Agenti

Agent (Claude Code mu říká *subagent*) je **pomocník s jedním úkolem**:
„spusť testy a nahlas jen výsledek“, „najdi, kde se tahle funkce
používá“, „zkontroluj diff“. Claude mu zadá úkol, pomocník pracuje ve
vlastním kontextu a Claude dostane zpět jen shrnutí. Konverzace tak zůstane
krátká a spotřeba nízká.

Klávesa `A` kdekoli (nebo **Agenti** v levém menu pod Živými relacemi)
otevře **Agenty**: pomocníky, které Claude Code najde pro vybranou
konverzaci.

| Klávesa | |
|---|---|
| `↑` / `↓` | vybere agenta; pod seznamem vidíš jeho soubor, nástroje a začátek promptu |
| `a` | přidá agenta (popíšeš ho sám, nebo ze šablony) |
| `e` / `Enter` | upraví vybraného agenta |
| `Delete` | smaže vybraného agenta |
| `Esc` | zavře okno |

## Přidání agenta

`a` otevře **Nový agent**. Pojmenuj agenta a řekni, kam se uloží: do tohoto
projektu, nebo do všech projektů; `Ctrl+S` ho vytvoří a nový agent se v
seznamu vybere. Začínáš jednou ze dvou věcí:

- **Popíšu ho sám** (výchozí): jednou větou řekni, co agent dělá a *kdy* ho
  má Claude použít, zda smí jen číst, také spouštět příkazy, nebo i měnit
  soubory, a jak důkladný má být (na jednoduché úkoly levnější model). ccc z
  toho složí pole souboru. Instrukce začnou jako stručná osnova; **Napsat
  instrukce pomocí Claude** je nechá napsat Claudem (jednorázové volání,
  které nemůže nic číst, zapisovat ani spouštět; započítá se do tvé
  spotřeby jako krátká otázka). Výsledek si přečti a změň, co chceš: dokud
  nestiskneš Vytvořit, nic se neuloží.
- **Šablona**; pod poli vidíš soubor přesně tak, jak se zapíše:

- **Revizor kódu**, **Spouštěč testů**, **Průzkumník kódu**,
  **Překladatel**: každý má jeden úkol, popis, který říká, kdy ho použít,
  jen nástroje, které úkol potřebuje, a tam, kde to stačí, levnější model.
- Existující soubor se nikdy nepřepíše.
- Soubor je potom tvůj: otevři ho v libovolném editoru a změň, co chceš.
  Claude Code změnu převezme během pár sekund.
- Claude Code sleduje jen složky agentů, které existovaly při jeho startu.
  První agent v nové složce potřebuje restart Claude Code tam, kde už běží
  (ccc to řekne).
- Aby celá konverzace běžela jako agent, viz
  [Nová konverzace](new-conversation.md#agent).

## Úprava a smazání

`e` (nebo `Enter`) upraví vybraného agenta: popis, nástroje, model a
instrukce. `Ctrl+E` otevře instrukce v tvém externím editoru. Uložení mění
**jen tyto čtyři věci**: jméno a všechna ostatní pole souboru (hooks, paměť,
MCP servery, tvoje komentáře) zůstanou přesně jak byla. Změnil-li soubor
mezitím někdo jiný, ccc ho nepřepíše; soubor, který je symbolický odkaz,
také nechá být (změň jeho cíl v editoru).

`Delete` agenta po potvrzení odstraní. Soubor se **nesmaže nadobro**:
přesune se do `~/.local/share/ccc/deleted-agents/` s datem v názvu, takže
ho můžeš vrátit zpět přesunem do složky agentů.

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
