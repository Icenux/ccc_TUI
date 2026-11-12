# Fronta úkolů

Každá konverzace má **frontu úkolů** — zprávy, které ccc posílá Claudovi
jednu po druhé, v téže konverzaci. Další odejde, jakmile Claude dokončí
předchozí, ať se díváš, nebo ne: můžeš si nachystat práci na celý večer
a ccc zavřít.

V [Konverzacích](conversations.md) stiskni `t` a otevře se fronta vybrané
konverzace.

![Fronta úkolů](../../../../docs/screenshots/tasks.png)

## Klávesy

| Klávesa | |
|---|---|
| `a` | přidá úkol |
| `e` / `Enter` | upraví vybraný úkol |
| `Delete` | odebere ho (v historii zůstane jako přeskočený) |
| `Ctrl+↑` / `Ctrl+↓` | posune ho nahoru / dolů |
| `p` | pozastaví / obnoví celou frontu (úkol, který už běží, doběhne) |
| `o` | zapne / vypne [automatické pokračování](limits.md) |
| `Esc` | zavře |

Horní řádek ukazuje, jestli je zapnuté automatické pokračování a jestli
fronta běží, nebo je pozastavená.

## Přidání úkolu

`a` otevře editor: napiš úkol (může mít víc řádků), nastav, kdy smí
začít, a ulož ho přes `Ctrl+S` (`Esc` ruší). `@` nabídne soubory
konverzace jako v poli pro zprávu — viz
[Zmínky souborů](conversations.md#zmínky-souborů).

**Spustit v:**

| Napíšeš | Začne |
|---|---|
| *(nic)* | co nejdřív |
| `23:00` | dnes ve 23:00 — zítra, pokud ten čas už minul |
| `2026-11-02 08:30` | v tento den a čas |
| `reset` | hned po příštím obnovení tvého 5hodinového limitu |

Úkol naplánovaný na později **drží své místo** ve frontě, takže úkoly za
ním čekají taky. Seznam úkolů má sloupec *Kdy* a řádek pod konverzací
říká, kdy začne další úkol (*Další úkol v 23:00*).

## Jak se úkol posílá

- **Claude Code vedle ccc** (konverzace založené přes `n`): ccc počká,
  až bude Claude nečinný, a úkol do něj vloží, jako bys ho napsal(a).
  Odpověď je přímo v Claude Code. Nečinný znamená, že Claude dokončil svůj
  tah — co nechal běžet na pozadí (shell, monitor), úkol nezdrží.
- **Konverzace na pozadí:** každý úkol je jeden běh `claude -p`; odpověď
  se objeví v přepisu v ccc.
  V git repozitáři se výsledek může commitnout — viz [Git](git.md).

Pole pro zprávu pod konverzací na pozadí přidává na konec stejné fronty.

## Když se něco pokazí

Když úkol selže, fronta se zastaví (`✖`) a čeká na tebe — sama to nikdy
nezkouší znovu a nepálí ti limit. `r` selhaný úkol spustí znovu. Viz
[Limity využití a zablokované fronty](limits.md).
