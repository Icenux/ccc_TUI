# Příkazová řádka

Všechno, co ccc umí, je dostupné z TUI; pár věcí jde i z shellu — třeba
ve skriptech nebo přes SSH. Příkazy jsou ve virtualenvu checkoutu
(`~/ccc/.venv/bin/`); přidej si ho do `PATH`, nebo je volej celou cestou.
Výpisy příkazů jsou v jazyce ccc. Zkratky změněné přes `ccc keys` se do
běžícího ccc dostanou po jeho restartu (`systemctl --user restart
ccc-tui`); změněné v Nastavení platí hned.

| Příkaz | |
|---|---|
| `ccc` | otevře ccc |
| `ccc add <složka> "<prompt>"` | zařadí prompt do konverzace té složky (běží na pozadí) |
| `ccc backup` | hned zazálohuje databázi |
| `ccc statusline [--then PŘÍKAZ]` | [stavový řádek](#stavovy-radek) Claude Code: řekne ccc, jak je zaplněný kontext konverzace |
| `ccc archive-analytics` | hned zapíše skončené měsíce do [měsíčního archivu analytiky](analytics.md) |
| `ccc config analytics-folder [CESTA\|off]` | ukáže / nastaví / vypne složku měsíčního archivu |
| `ccc config continue-text ["…"]` | ukáže / nastaví zprávu pro pokračování posílanou po limitu využití |
| `ccc config usage-reserve [N\|off]` | ukáže / nastaví [rezervu využití](limits.md) |
| `ccc config language [auto\|en\|cs]` | ukáže / nastaví jazyk ccc (auto = podle systému) |
| `ccc config telegram-token [TOKEN]` | ukáže (zamaskovaný) / nastaví token Telegram bota |
| `ccc config telegram-chat [ID]` | ukáže / nastaví ID Telegram chatu |
| `ccc config telegram-test` | pošle testovací zprávu na Telegram |
| `ccc config discord-webhook [URL\|off]` | ukáže (zamaskovaný) / nastaví / odebere webhook Discordu |
| `ccc config discord-test` | pošle testovací zprávu na Discord |
| `ccc keys list` | všechny [klávesové zkratky](settings.md#klávesové-zkratky): id akce, klávesa (`*` = změněná), co dělá |
| `ccc keys check KLÁVESA [AKCE]` | je klávesa volná? (kdo ji má, nebo proč nejde použít); s id akce: volná pro tu akci — při kolizi návratový kód 1 |
| `ccc keys set AKCE KLÁVESA` | dá akci novou klávesu — při kolizi odmítne (návratový kód 1) |
| `ccc keys reset [AKCE]` | zpět na výchozí klávesu (jedna akce, nebo všechny) |
| `ccc keys export SLOŽKA` | zapíše zkratky do `SLOŽKA/ccc-keys.json` |
| `ccc keys import CESTA` | použije zkratky ze souboru `ccc-keys.json` (nebo ze složky s ním) — při kolizi nenaimportuje nic |
| `ccc routine list` | všechny [routiny](routines.md): id, zapnuto / vypnuto, konverzace, plán, další běh |
| `ccc routine add KONVERZACE "PROMPT" --daily 18:00` | přidá konverzaci (id nebo název) routinu; plán je jedno z `--every 30m` / `2h`, `--hourly`, `--daily [HH:MM]`, `--weekdays [HH:MM]`, `--on "po 08:00"`, `--cron "0 22 * * *"`; `--fresh` = při každém běhu nová konverzace, `--name` = název |
| `ccc routine run ROUTINA` | spustí ji hned (id nebo název) |
| `ccc routine on ROUTINA` / `off ROUTINA` | zapne / vypne ji |
| `ccc routine remove ROUTINA` | smaže ji |
| `ccc review start KONVERZACE ["co zkontrolovat"]` | požádá o [recenzi](review.md) (daemon ji spustí, až konverzace nic nedělá); `--until-pass [--rounds N]` = poslat nálezy zpět a recenzovat znovu; `--wait` = počkat na verdikt, návratový kód 0 PASS (nebo nic nového), 3 FAIL, 4 PARTIAL, 1 chyba |
| `ccc review list [KONVERZACE]` | recenze od nejnovější: id, čas, verdikt, co se recenzovalo |
| `ccc review show RECENZE` | zpráva recenze |
| `ccc review stop KONVERZACE` | zastaví její recenzi (smyčku) |
| `ccc goal start KONVERZACE "CÍL"` | spustí [smyčku k cíli](goal.md); `--check "pytest -q"` = spustit po každém kole, `--rounds N` = nejvýš N kol, `--wait` = počkat do konce, návratový kód 0, když je cíl splněný |
| `ccc goal list [KONVERZACE]` | cíle od nejnovějšího: id, začátek, kolo, stav |
| `ccc goal stop KONVERZACE` | zastaví její smyčku k cíli |
| `ccc runs KONVERZACE` | běhy konverzace: id, čas, stav, commit (`↺` = vrácený), úkol |
| `ccc revert BĚH [--tell]` | [vrátí běh](git.md#vrácení-běhu): jeho commit vrátí novým commitem; `--tell` = zařadí i poznámku pro Clauda |
| `ccc workflow templates` | šablony [workflow](workflows.md) a jejich kroky |
| `ccc workflow start KONVERZACE ŠABLONA "ÚKOL"` | spustí workflow; `--check "pytest -q"`, `--rounds N`, `--wait` = počkat do konce (návratový kód 0, když prošlo) |
| `ccc workflow list [KONVERZACE]` / `show BĚH` | běhy od nejnovějšího / kroky běhu a co který agent řekl |
| `ccc workflow approve KONVERZACE` / `reject KONVERZACE` | odpoví na krok, na kterém běh čeká (`--note "…"` pro další krok) |
| `ccc workflow stop KONVERZACE` | zastaví její workflow |
| `ccc workflow import SOUBOR` / `export ŠABLONA SLOŽKA` / `delete ŠABLONA` | vlastní šablony jako JSON |
| `ccc api serve [--port N]` | spustí [API pro aplikaci pro Android](android-api.md) (normálně ho spouští `ccc-api.service`) |
| `ccc api token [--new]` | vytvoří token, kterým se aplikace přihlásí (ukáže se jen tehdy); `--new` = nový, všechna zařízení se odhlásí |
| `ccc api pair [--server URL] [--image [SOUBOR]]` | ukáže QR kód, kterým se aplikace přihlásí (platí 5 minut, jednou); `--image` ho uloží jako obrázek, když se kód z terminálu nečte |
| `ccc api devices` / `ccc api revoke ID` | vypíše přihlášená zařízení (`--all`: i odhlášená, historie) / jedno odhlásí |
| `ccc-daemon` | samotný daemon (normálně ho spouští `ccc.service`) |

## Stavový řádek

`ccc statusline` způsobí, že Claude Code řekne ccc, jak je zaplněné kontextové
okno interaktivní konverzace — skutečná hodnota, ne odhad. [Appka](android-api.md)
ji ukáže u konverzace. Dej ho do `~/.claude/settings.json`:

```json
"statusLine": {"type": "command", "command": "ccc statusline"}
```

Máš už vlastní stavový řádek? Předej ho dál, dostane stejný vstup a Claude Code
ukáže to, co vypíše:

```json
"statusLine": {"type": "command", "command": "ccc statusline --then '~/.claude/muj-radek.sh'"}
```

Stavový řádek spouští jen interaktivní Claude Code (ne `claude -p`) a hodnota je
tak čerstvá, jak často ji Claude Code obnovuje.
