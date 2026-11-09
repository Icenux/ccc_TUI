# Přehled

Přehled (`d`) ukazuje všechny konverzace a to, co zrovna dělají. Slouží
jen k dívání — s konverzací pracuješ v [Konverzacích](conversations.md).

![Přehled](../../../../docs/screenshots/dashboard.png)

## Souhrn

Horní řádek sčítá všechny konverzace: na kolika Claude pracuje
(**běží**), kolik úkolů čeká v jejich frontách (**čeká**), kolik se jich
zastavilo chybou (**zablokované**, červeně), kolik jich **čeká na tebe**
(žlutě: schválení nástroje, chyba, limit, dokončená odpověď, na kterou ses
ještě nepodíval(a) — stejné, na které skáče klávesa Alt+N) a kolik tokenů
dnes spotřebovaly.

## Tabulka

Jeden řádek na konverzaci (archivované jsou v [Archivu](archive.md)):

| Sloupec | |
|---|---|
| Projekt | název konverzace se značkou (viz níže) |
| Stav | co dělá — viz tabulka níže |
| Cíl | její poslední [smyčka k cíli](goal.md): `◎ 3/5` (kolo / kol) za běhu, den po skončení `✅ splněno 3/5` / `⛔ zaseknuto 4/5` / `⛔ došla kola 5/5` / `✖ selhalo` / `■ zastaveno`; jinak `–` |
| Čeká | úkoly čekající v její [frontě](tasks.md) |
| Tokeny dnes | tokeny, které dnes spotřebovala |
| Git | větev, neuložené řádky (`+12−3`) a commity před `↑` / za `↓` základní větví; `–` mimo git a u konverzace, kde Claude nesmí sám spouštět příkazy (ccc spouští git jen tam, viz [Git](git.md)) |
| Cesta | složka projektu, ve které Claude pracuje, zkrácená zepředu — poslední složka je vidět vždy |

**Které sloupce** — Nastavení → Přehled: jedna volba pro terminálovou
verzi ccc (aplikace pro Android má vlastní). Název je vidět vždy;
výchozí je ukázat i všechno ostatní.

**Značky** před názvem — stejné jako v seznamu Konverzací:

| Značka | Význam |
|---|---|
| `●` | Claude pracuje |
| `‖` | čeká na obnovení limitu využití, nebo je pozastavená |
| `✖` | zastavila se chybou — potřebuje tě |
| `?` | Claude čeká, až [schválíš nástroj](approvals.md) |
| `◆` | nová odpověď, kterou jsi ještě neviděl(a) |
| `○` | nečinná a poslední odpověď jsi už viděl(a) |

V seznamu Konverzací znamená `·3` za názvem tři úkoly ve frontě.

**Stav:**

| Stav | |
|---|---|
| `nečinná` | nemá co dělat; další úkol začne, jakmile nějaký přidáš |
| `běží` | Claude pracuje na úkolu |
| `čeká na opakování` | čeká na obnovení limitu využití; pak sama pokračuje |
| `pauza na limitu` | zastavila se na limitu využití s vypnutým automatickým pokračováním — čeká na tebe |
| `zablokovaná chybou` | zastavila se chybou — čeká na tebe |

Co s posledními dvěma: [Limity využití a zablokované fronty](limits.md).

## Detail fronty

`Enter` na řádku otevře detail konverzace:

- její nastavení (složka, volby Claude Code, automatické pokračování)
  a u konverzace se [vzdáleným ovládáním](remote-control.md) odkaz, jak
  ji otevřít v telefonu;
- **Teď**: jestli Claude pracuje, čeká na tvé schválení nebo na limit,
  kolik zpráv je ve frontě a co se v konverzaci stalo naposledy;
- **Změny**: větev složky a soubory s necommitnutými změnami, s počtem
  přidaných a odebraných řádků (jen ve složce s gitem a v konverzacích
  spuštěných s *Přeskočit všechny kontroly oprávnění*, protože git ccc
  spouští jen tam);
- **Spotřeba**: tokeny za posledních 14 dní a použité modely;
- **Chyby**: poslední selhané běhy a co konverzaci teď blokuje.
  Stránka se obnovuje sama;
- `+` přidá konverzaci úkol, `r` ji znovu rozběhne, když je zablokovaná
  nebo pozastavená na limitu. `Esc` vrací zpět.

![Detail fronty](../../../../docs/screenshots/queue_detail.png)

## Prázdný Přehled

Před první konverzací ukazuje Přehled místo tabulky **Začínáme** — tři
kroky na začátek. Stiskni `n`.
