# Nastavení a notifikace

`Ctrl+P` otevře Nastavení — všechno, co se dá změnit, v jednom okně.
Změň, co potřebuješ, a stiskni `Ctrl+S` (*Uložit*). `Esc` (*Zrušit*)
změny zahodí. Tlačítka jako *Poslat testovací zprávu*, *Vypnout* nebo
*Zálohovat hned* fungují okamžitě.

![Nastavení](../../../../docs/screenshots/settings.png)

## Sekce

Okno má záložku pro každý druh nastavení (přepínají se `←` `→` nebo
klikem); *Uložit* použije všechny záložky najednou:

| Záložka | Sekce |
|---|---|
| Obecné | Vzhled, Externí editor, Přehled, Klávesové zkratky |
| Fronty | Fronty |
| Notifikace | Telegram, Discord |
| Předvolby | Přednastavené prompty, Předvolby nové konverzace |
| Data | Exporty, Měsíční archiv analytiky, Data |
| Připojení a aktualizace | Spárovat nové zařízení, Zařízení, Aktualizace |

*Nastavení ccc* v aplikaci pro Android má stejné druhy záložek (a navíc
*Připojení*, které otevře vlastní obrazovku aplikace s přihlášením a
zařízeními).

| Sekce | |
|---|---|
| Vzhled | jazyk — *Automaticky* se řídí systémem (`LANG`), uložení jiného jazyka restartuje okno ccc |
| Externí editor | editor, ve kterém `Ctrl+E` otevře text pole — viz níže |
| Notifikace přes Telegram | viz níže |
| Notifikace přes Discord | viz níže |
| Fronty | text *pokračuj* posílaný po limitu využití a rezerva využití — viz [Limity využití](limits.md); model [recenzenta](review.md); *Skupiny v seznamu* — podle projektu, nebo jen tvé vlastní (viz [Konverzace](conversations.md#skupiny)) |
| Přednastavené prompty | tvé prompty na `Alt+1` … `Alt+9` — viz níže |
| Předvolby nové konverzace | smaže předvolby uložené v okně [Nová konverzace](new-conversation.md) |
| Klávesové zkratky | změna libovolné klávesy, export / import — viz níže |
| Exporty | složka pro exportované konverzace (`e`) a snímky obrazovky (`Alt+S`); volitelně posílat každý export na Telegram / Discord |
| Měsíční archiv analytiky | viz [Analytika](analytics.md) |
| Aktualizace | hledání nových verzí ccc, zapnuto / vypnuto — viz níže |
| Spárovat nové zařízení | QR kód pro aplikaci pro Android — viz [Aplikace pro Android a API](android-api.md) |
| Zařízení | seznam telefonů přihlášených k API pro aplikaci pro Android přímo na záložce: změna role, odhlášení jednoho, historie, aktivita — viz [Aplikace pro Android a API](android-api.md) |
| Data | kde je databáze a její denní zálohy, *Zálohovat hned* |

Stejné hodnoty se dají nastavit i z příkazové řádky, viz
[Příkazová řádka](command-line.md).

## Přednastavené prompty

Prompty, které píšeš pořád dokola — „pokračuj“, „spusť testy a oprav, co
selže“, „zkontroluj tenhle soubor: “ — uložené jednou, použitelné
v každé konverzaci přes `Alt+1` … `Alt+9`.

- **Nastavení:** *Přednastavené prompty* → *Přidat* / *Upravit*:
  **název** (ukazuje se v liště), **prompt** a zaškrtávátko
  **„Text se odešle do konverzace“**. *Nahoru* / *Dolů* určují pořadí — první je `Alt+1`.
  Nejvýš 9.
- **Použití:** stiskni klávesu, nebo klikni na předvolbu v liště.
  - Prompty `⏎` se hned **odešlou**.
  - Prompty `✎` se **vloží do pole pro zprávu** (nebo do vstupu Clauda),
    abys je dopsal(a), třeba přidal(a) název souboru.

![Editor přednastaveného promptu](../../../../docs/screenshots/prompt_preset.png)

## Externí editor

Dlouhé zadání se líp píše ve skutečném editoru. `Ctrl+E` v libovolném
textovém poli — v poli pro zprávu v Konverzacích, v oknech úkolu,
routiny, cíle, recenze, workflow a dotazu — otevře jeho text v editoru
vybraném tady. ccc ustoupí, dokud editor nezavřeš (i v tmuxu a přes
SSH); co uložíš, se vrátí do pole — neodešle se, můžeš ho ještě upravit.
Zavření bez uložení nic nezmění.

*Editor* nabízí jen editory nainstalované na tomto stroji (`nano`,
`micro`, `vim`, `nvim`, `emacs -nw`, `hx`, `kak`…), grafické
(`code --wait`, `gedit --wait`…) jen když je k dispozici displej.
*Automaticky* použije `$VISUAL` / `$EDITOR`, jinak první nalezený. Text
jde přes soubor ve složce ccc, který můžeš číst jen ty, a hned potom se
smaže. `Ctrl+E` jde přemapovat jako každá klávesa.

## Klávesové zkratky

Každou klávesu v ccc jde změnit: *Klávesové zkratky* → *Upravit
zkratky…* otevře okno se všemi akcemi a jejich klávesami (změněné jsou
žlutě).

- **Změna:** vyber akci, pak *Stisknout klávesu* a stiskni novou klávesu
  (jakoukoli — i Esc nebo Ctrl+P), nebo napiš její název (`Ctrl+O`,
  `Alt+S`, `F5`, `+`) a *Nastavit*. *Výchozí* vrátí jednu, *Vše výchozí*
  všechny.
- **Nejdřív kontrola:** klávesa, která koliduje, se odmítne a okno řekne
  proč — nikdy se neuloží. Kolize je:
  - jiná akce na stejné klávese tam, kde fungují obě zároveň (např. dva
    pohledy, nebo pohled a klávesa, která funguje všude),
  - klávesa, kterou terminál nerozliší (Ctrl+I je Tab, Ctrl+M je Enter,
    Ctrl+H je Backspace), Ctrl+C, Ctrl+B (prefix tmuxu) nebo
    Ctrl+Shift+C / V (kopírovat / vložit),
  - klávesa, kterou potřebují seznamy a textová pole (šipky, Enter, Tab,
    mezerník, editační klávesy jako Ctrl+A / E / K / U / W),
  - u kláves, které fungují i z panelu Claude Code (přepnutí, zavření
    Claude, přednastavené prompty, Nastavení, snímek obrazovky, zavření
    ccc): klávesa Claude Code (Ctrl+T, Ctrl+O, Ctrl+R, Alt+P, …) nebo
    klávesa bez Ctrl / Alt (F-klávesy jsou v pořádku) — do Claude Code by
    už nešla napsat.
- **Uložit** (`Ctrl+S`) a nové klávesy platí hned — ukazuje je menu,
  tlačítka i `?`. Toto okno ukládá samo, nezávisle na Uložit v Nastavení.
- **Export / Import:** napiš složku (předvyplněná je složka exportů) —
  *Exportovat* do ní zapíše `ccc-keys.json`, *Importovat* ho z ní načte
  (nebo ze souboru, který zadáš) a zkontroluje stejně: jediná kolize a
  nenaimportuje se nic. Soubor přenes na jiný počítač a máš tam své
  klávesy.

Totéž z příkazové řádky: `ccc keys` (viz [Příkazová řádka](command-line.md)).
Ostatní stránky příručky uvádějí výchozí klávesy.

## Notifikace přes Telegram

ccc ti napíše, když konverzace **dokončí frontu**, **zastaví se chybou**,
**potřebuje tě** (pauza na limitu, čekající schválení nástroje) nebo
**narazí na limit využití** a později **pokračuje**.

1. V Telegramu založ bota přes [@BotFather](https://t.me/BotFather)
   (`/newbot`) a zkopíruj jeho token.
2. Pošli botovi libovolnou zprávu, pak v prohlížeči otevři
   `https://api.telegram.org/bot<TOKEN>/getUpdates` a zkopíruj číslo za
   `"chat":{"id":`.
3. Obojí vyplň v Nastavení, stiskni *Poslat testovací zprávu* a pak
   *Uložit*.

## Notifikace přes Discord

Stejné zprávy můžou chodit do kanálu na Discordu — místo Telegramu,
nebo k němu.

1. V Discordu: nastavení kanálu → *Integrace* → *Webhooky* → *Nový
   webhook* → *Kopírovat URL webhooku*.
2. Vlož ji v Nastavení, stiskni *Poslat testovací zprávu* a pak
   *Uložit*.

URL nikomu nedávej: kdo ji má, může do kanálu psát. Tokeny a webhooky
se ukládají v databázi ccc, zobrazují se zamaskované a nikdy se
nelogují.

## Které zprávy která služba dostane

Pod každou službou v Nastavení (a v aplikaci: *Nastavení → Oznámení*)
otevře *Vybrat zprávy…* okno se zaškrtávátkem pro každý druh zprávy.
Telegram a Discord mají **každý svůj výběr**, takže jedna služba může
dostávat všechno a druhá jen to, co potřebuje tebe. V aplikaci se ukládá
tlačítkem *Uložit*, v terminálu hned (`Ctrl+S`).

| Druh | O čem informuje | Výchozí |
|---|---|---|
| Fronta | fronta doběhla nebo se zastavila kvůli chybě | zapnuto |
| Limity využití | čekání na obnovu limitu a pokračování | zapnuto |
| Potřebuje tebe | nástroj ke schválení, brána workflow, odpověď na otázku | zapnuto |
| Výsledky | workflow, cíl nebo review skončilo | zapnuto |
| Aktualizace | je dostupná nová verze ccc | zapnuto |
| Konverzace začala | konverzace začne pracovat po 10 a více minutách klidu | vypnuto |
| Rutiny | rutina se spustila nebo se přeskočila | vypnuto |
| Zařízení | spárovalo se nové zařízení, odhlásilo se, nebo se změnila jeho role | zapnuto |
| Zabezpečení | špatný párovací kód nebo token, zakázaný požadavek | zapnuto |
| Zdraví | selhalá záloha, nové varování v `ccc api doctor` | vypnuto |
| Denní souhrn | po 8:00, co za posledních 24 hodin běželo | vypnuto |

Druh, který jsi nikdy nenastavil, má výchozí hodnotu, takže se nic
nezmění, dokud něco nezaškrtneš nebo neodškrtneš.

## Tajemství se maskují

Co z ccc odchází — každá zpráva na Telegram / Discord a každá exportovaná
konverzace (`e`) — má tajemství nejdřív nahrazená za `[redacted]`: API
klíče a tokeny známého tvaru (`sk-ant-…`, `ghp_…`, `AKIA…`, Slack, Stripe,
JWT, bloky soukromých klíčů…), hesla v URL, hlavičky `Bearer …` a hodnoty
pojmenované jako tajemství (`DB_PASSWORD=…`, `"client_secret": …`).
Rozpoznatelný začátek zůstane, takže z `ANTHROPIC_API_KEY=sk-ant-[redacted]`
pořád poznáš, co tam bylo. Je to pojistka, ne záruka: tajemství bez
známého tvaru a bez výmluvného názvu projde. Snímky obrazovky (`Alt+S`)
jsou obrázky obrazovky a nemaskují se.

## Zálohy

Daemon každý den zálohuje databázi ccc a drží posledních 14
(`~/.local/share/ccc/backups/`). Jak zálohu obnovit, viz
[Řešení potíží](troubleshooting.md).

## Aktualizace ccc

Nová verze je **vydání (release) na GitHubu** podle stejných pravidel jako u
Android aplikace: tag je verze `X.Y.Z` (předpona `v` nevadí), vydání není
koncept ani předběžné a má přiložený `ccc.tar.gz` (sestaví ho release build
z tagu, [viz níže](#vydání-verze)). Verze se porovnávají jako čísla. ccc, jehož
verze není číslo (ruční build, `0.0.0-dev`), bere za svou verzi nejnovější tag
svého checkoutu; bez tagu se nikdy neptá.

Daemon se jednou za pár hodin podívá (jeden dotaz na GitHub, bez tokenu, nic
se nemění) a když je novější vydání, dá ti vědět:

- v ccc: žlutý řádek *⬆ Aktualizace* pod titulkem na každé obrazovce —
  `U` (nebo klik na něj) otevře okno **Aktualizace** s poznámkami k vydání;
- v telefonu: banner v seznamu Android aplikace se stejným tlačítkem
  ([Android aplikace a API](android-api.md));
- v Telegramu a Discordu, jednou pro každou novou verzi.

*Aktualizovat teď* přepne ccc na to vydání a restartuje ho (Claude Code
interaktivní konverzace běží dál; ccc a API jsou za chvíli zpátky). Vždy se
zeptá a nikdy neběží samo. Jak se přepne, záleží na tom, jak je ccc
nainstalován:

- **git checkout** (`bin/install.sh`, obvyklý případ): stáhne tag vydání a
  checkout na něj převine (fast-forward) — ne na nejnovější `main` — a pak
  spustí `bin/install.sh` se stejnými volbami, s jakými byl ccc
  nainstalován. Odmítne — a řekne proč —, když checkout není na `main`, má
  neuložené nebo nepushnuté změny, vydání na něj nenavazuje převinutím, nebo
  běží úkol;
- **systémový balíček** (`.deb`, `apt install ccc`): aktualizuje ho správce
  balíčků — `sudo apt update && sudo apt upgrade` a potom
  `systemctl --user restart ccc ccc-tui`. ccc jen oznámí, že je nové vydání;
  *Aktualizovat teď* to vysvětlí a nic nezmění. Po `apt upgrade` služby ještě
  běží ze starého kódu, dokud se nerestartují: ccc si toho všimne (soubory na
  disku mají jinou verzi než běžící TUI, API nebo daemon) a pod *daemon běží*
  ukáže **↻ je třeba restart — klikni** — klik restartuje `ccc`, `ccc-tui`
  a `ccc-api`, ale jen když neběží žádný úkol (stejný řádek a tlačítko má
  telefon). Konverzace běží dál. První upgrade *na* verzi s tímto řádkem ho
  ještě ukázat neumí; jednou restartuj ručně:
  `systemctl --user restart ccc ccc-tui ccc-api`;
- **balíček** (pip / pipx): stáhne `ccc.tar.gz` z vydání a nainstaluje ho
  pomocí `pip install` do prostředí, odkud ccc běží.

Výstup instalace je v `~/.local/share/ccc/update.log`. Android aplikace se
aktualizuje zvlášť, z vydání jejího vlastního repozitáře
([Aplikace pro Android a API](android-api.md#aktualizace-aplikace)).

*Nastavení → Aktualizace → Hledat aktualizace* hledání (i tyto zprávy)
vypne; když GitHub není dostupný, okno ukáže, co selhalo.

### Vydání verze

`~/Documents/CC/bin/ccc-android-release.sh 0.2.0` (nebo prostě push tagu
`0.2.0`, bez `v`) spustí `.github/workflows/release.yml`: vytvoří vydání (nebo
ponechá to, které jsi založil ručně), spustí testy, vepíše tag do
`ccc.__version__` a přiloží `ccc.tar.gz`. Artefakt, který už je u vydání, se
nikdy nenahrazuje.

Stejný běh sestaví a otestuje i systémový balíček (`ccc_all.deb`, pro APT
repozitář Icenux), ale nepřiloží ho: podpisový klíč zůstává na počítači
správce (`~/.config/ccc-release/`, mimo repozitář). Až je běh zelený,
`~/Documents/CC/bin/ccc-deb-sign-upload.sh 0.2.0` balíček stáhne, podepíše
`SHA256SUMS` a k vydání přiloží `ccc_all.deb`, `SHA256SUMS` a
`SHA256SUMS.asc`; Icenux si balíček vezme odtud.
