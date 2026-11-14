# Začínáme

ccc je domov pro tvou práci s Claude Code, která **běží dál i bez tebe**.
Ve svých projektech zakládáš konverzace s Claude Code, řadíš jim úkoly
do fronty a daemon na pozadí frontu postupně zpracovává: přečká limity
využití a sám pokračuje, zeptá se tě, když Claude potřebuje schválení,
commitne hotovou práci a dá ti vědět přes Telegram nebo Discord.

![Prázdný Přehled: Začínáme](../../../../docs/screenshots/welcome.png)

## Dvě části: daemon a TUI

- **Daemon** (`ccc.service`) dělá veškerou práci. Běží pořád na pozadí —
  i když není otevřené žádné okno ccc, když se odhlásíš ze SSH i po
  restartu počítače.
- **TUI** — to, na co se díváš — je jen okno do něj: tady čteš
  konverzace, řadíš úkoly a měníš nastavení. Můžeš ho kdykoli zavřít;
  nic se nezastaví.

Postranní panel upozorní, když daemon neběží. Pokud
tam stojí *daemon neběží*, nic se neděje — viz
[Řešení potíží](troubleshooting.md).

## Otevření a zavření ccc

- `ccc` v libovolném terminálu (místně i přes SSH) otevře ccc. ccc je
  vždycky jen jedno: každý terminál ukazuje stejnou obrazovku.
- `Ctrl+Q` ho v tomto terminálu zavře. ccc běží dál a úkoly ve frontě
  taky; `ccc` ho zase otevře.

## Tvoje první konverzace

1. Stiskni `n`. Vyber **složku projektu**, ve které má Claude pracovat,
   zaškrtni volby pro Claude Code (model, oprávnění, …) a stiskni
   **Spustit**. Viz [Zahájení konverzace](new-conversation.md).
2. Jsi v **Konverzacích**: vpravo od menu ccc se otevře skutečné
   Claude Code. Mluv s ním přesně jako v terminálu. `Alt+0` přepíná
   klávesnici mezi ccc a Claudem. Viz [Konverzace](conversations.md).
3. Chceš, aby Claude potom udělal ještě něco? Stiskni `t` a přidej
   **úkoly**: spouští se jeden po druhém, každý hned, jak Claude dokončí
   předchozí — i v noci, i po obnovení limitu využití. Viz
   [Fronta úkolů](tasks.md).

## Jak se vyznat

Každá obrazovka vypadá stejně:

- **Vlevo:** menu ccc — obrazovky s klávesami (`d` Přehled, `c`
  Konverzace, `h` Archiv, `s` Živé relace, `a` Analytika; dají se
  i kliknout), vlastní seznam obrazovky a její tlačítka.
- **Vpravo:** samotná obrazovka.
- **Spodní lišta:** klávesy obrazovky, na které jsi — jen ty, které tam
  něco dělají — a k tomu `?` Nápověda, `Ctrl+Q` Zavřít a `Ctrl+P`
  Nastavení; když se nevejdou, pokračují na druhém řádku. Kliknutím
  klávesu stiskneš.

Klávesy bez `Ctrl` nebo `Alt` (třeba `n`, `t`) nefungují, když píšeš do
textového pole — nejdřív klikni do seznamu vlevo.

## Co dál

- [Limity využití a zablokované fronty](limits.md) — co se stane, když
  narazíš na limit Clauda, a jak si část nechat pro sebe.
- [Nastavení a notifikace](settings.md) — jazyk, zprávy
  na Telegram / Discord, přednastavené prompty.
- [Vzdálené ovládání (telefon)](remote-control.md) — pokračuj
  v konverzaci z aplikace Claude.
- [Klávesové zkratky](keys.md) — všechny klávesy na jedné stránce.
