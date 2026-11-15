# Routiny

Routina je prompt, který ccc zařazuje **podle plánu**: večerní kontrola
denních změn, kontrola každou hodinu, shrnutí v pracovní dny v šest.
Routiny spouští daemon, takže běží **i se zavřeným ccc**, dokud je
počítač zapnutý.

Klávesa `t` kdekoli otevře **Úkoly a routiny**: routiny jsou v dolní
tabulce s plánem, dalším a posledním během (`Tab` na ni přepne). Routina
zůstává, dokud ji nesmažeš.

| Klávesa | |
|---|---|
| `+` | přidá routinu |
| `e` / `Enter` | upraví ji |
| `mezerník` | zapne / vypne ji |
| `r` | spustí ji hned (další čas podle plánu zůstane) |
| `Delete` | smaže ji (úkoly, které už zařadila, zůstanou) |
| `Esc` | zavře okno |

## Přidání routiny

- **Název**: pro seznam (prázdný = začátek promptu).
- **Konverzace**: ke které konverzaci patří (předvyplní se ta vybraná
  v Konverzacích) a kde běží:
  - **V této konverzaci**: prompt přijde do její
    [fronty úkolů](tasks.md), jako úkol přidaný ručně. Claude si pamatuje
    předchozí běhy, což se hodí na „co se změnilo od včera?“.
  - **Při každém běhu nová konverzace**: nová konverzace na pozadí ve
    stejné složce, se stejnými volbami (model, oprávnění). Každý běh
    začíná načisto; po dokončení se archivuje. Zablokovaná zůstane, ať
    vidíš, co se pokazilo. V seznamu je označená `✚`.
- **Kdy**: každých N minut, každých N hodin, každý den v HH:MM, pondělí až
  pátek v HH:MM, jednou týdně (`po 08:00`, i anglicky `monday 08:00`)
  nebo vzor cronu (`0 22 * * *`). Pod polem uvidíš plán slovy a další
  běh, případně proč plán nejde použít.
- **Prompt**: co Claude dostane.

`Ctrl+S` uloží, `Esc` zruší.

## Jak běhy fungují

- Úkol z routiny je obyčejný úkol: [limity využití](limits.md),
  [commit](git.md) i notifikace fungují jako u každého jiného. Ve
  [frontě úkolů](tasks.md) je označený `↻`.
- **Zmeškané běhy** (daemon nebo počítač neběžel): po návratu daemonu se
  dohoní **jednou**, pak zase podle plánu.
- **Ještě běží**: dokud předchozí běh routiny neskončil, nový se
  přeskočí. Seznam to ukáže a přeskočené běhy počítá.
- Routina, jejíž konverzace je v [Archivu](archive.md), neběží; smazání
  konverzace smaže i její routiny.

Z příkazové řádky: `ccc routine` (viz [Příkazová řádka](command-line.md)).
