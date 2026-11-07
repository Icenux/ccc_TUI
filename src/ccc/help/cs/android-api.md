# Aplikace pro Android a API

ccc má aplikaci pro Android (vlastní repozitář
[ccc_android](https://github.com/Luquas95/ccc_android) — jak ji nainstalovat
a ověřit stažení: její README).
S ccc mluví přes malé HTTP API: konverzace a jejich zprávy, odeslání
zprávy, schvalování nástrojů, nová konverzace, limity využití — a proud
změn, takže telefon novou odpověď nebo žádost o schválení uvidí hned.

API stejně jako TUI jen čte a zapisuje databázi ccc; práci dělá daemon.
Je dostupné jen z tvé sítě [Tailscale](https://tailscale.com) a každý
požadavek potřebuje token.

## Zapnutí

1. Nainstaluj jeho službu: `bin/install.sh --api` (s ostatními volbami,
   které používáš). API pak běží jako `ccc-api.service` a poslouchá na
   `127.0.0.1:8737` — jen na tomto počítači.
2. Vystav ho do tailnetu s HTTPS: `tailscale serve --bg 8737`. Pak je na
   `https://<počítač>.<tailnet>.ts.net/api/v1/` — adresu vypíše
   `tailscale serve status`. Proxy běží na tomto počítači, proto ccc
   bere adresu telefonu pro *Zařízení* z její hlavičky `X-Forwarded-For`
   (a jen ze spojení z loopbacku).
3. `ccc api token` vytvoří token, kterým se aplikace přihlásí, a vypíše
   ho — **jen tentokrát**: ccc si drží jen jeho otisk (SHA-256), takže ho
   z databáze nikdo nepřečte. Je token pryč, nebo se ztratil telefon?
   `ccc api token --new` vytvoří nový; všechna dosavadní zařízení se
   odhlásí hned, i otevřená spojení. Každé zařízení má vlastní token a dá
   se odhlásit samo: `ccc api devices` je vypíše, `ccc api revoke ID`
   odhlásí jedno. Odhlášení řádek nemaže: zůstane jako historie jen ke
   čtení (`ccc api devices --all`) s údajem, kdy a jak bylo zařízení
   odhlášeno — v aplikaci, na počítači, nebo novým tokenem — a jeho token
   přestane platit. Připojí-li se přihlášená aplikace jiným tokenem (nový QR kód nebo
   ručně), nejdřív odhlásí své staré zařízení, aby se tentýž telefon
   v seznamu nehromadil. Aplikace ukazuje stejnou historii v *Připojení →
   Zařízení → Historie*. Aplikace se zneplatněným tokenem to pozná při dalším
   požadavku (skončí i otevřený proud událostí): token zapomene,
   přestane se ptát a ukáže přihlašovací obrazovku.
4. V aplikaci zadej adresu (stačí `<počítač>.<tailnet>.ts.net`) a token
   — nebo kroky 3 a 4 přeskoč a **spáruj telefon QR kódem**:

   *Nastavení → Připojení a aktualizace → Spárovat nové zařízení* (nebo `ccc api pair`
   v terminálu) ukáže QR kód; v aplikaci zvol *Připojení → Skenovat QR*.
   Kód obsahuje adresu API a jednorázový párovací kód, platný 5 minut a
   pro jeden telefon; aplikace ho vymění za vlastní token (`POST /pair`,
   jediný požadavek bez tokenu), takže se nic nepíše a token obrázkem
   neprochází. QR kód má vlastní okno; jakmile se telefon spáruje, samo se zavře
   a seznam Zařízení nové zařízení zvýrazní. Seznam (hned pod tím, na téže záložce) vypisuje všechny
   přihlášené telefony (název, kdy se spároval, naposledy vidět, adresa),
   samo se obnovuje a *Odhlásit* jeden odebere. *Historie* přepne na dřív odhlášená zařízení
   (kdy a jak: v aplikaci, na počítači, novým tokenem) — jen ke čtení,
   nic z ní nejde smazat. Adresu ccc najde v `tailscale serve status`; když ne,
   zadej ji: `ccc api pair --server https://<počítač>.<tailnet>.ts.net`.
   *Nastavení → Připojení a aktualizace → Zařízení* vypíše přihlášené
   telefony a jeden odhlásí.

Bez služby ho spustí v terminálu `ccc api serve` (`--port` pro jiný
port).

## V aplikaci

Všechno, co umí okno ccc, upravené pro telefon: konverzace, fronta
úkolů, běhy a diffy, recenze, cíle, workflow, rutiny, živé
relace, analytika, nastavení — a Claude Code interaktivní konverzace
živě, s klávesami pro jeho nabídky.

**Oznámení** (Připojení → *Oznamovat, i když je aplikace zavřená*):
aplikace drží spojení s ccc otevřené i na pozadí a dá ti vědět
o schválení — odpovíš přímo v oznámení —, chybě, limitu využití nebo
hotové odpovědi. Žádný push server se nepoužívá; na Xiaomi / HyperOS
aplikaci povol běh na pozadí, jinak ji systém zastaví.

**Aktualizace:** `GET /status` má `update` (novější vydání na GitHubu: `version`, `installed`, `notes`, `can_apply`, proč ne; `behind`, `remote_head` a `commits` zůstávají pro starší aplikace: 1, verze a první řádky poznámek); `GET /status` má také `restart_needed` (ccc bylo aktualizováno pod běžícími procesy) a `POST /restart` restartuje služby ccc — jen když neběží žádný úkol (jinak 409 s důvodem); role admin. `POST /update/check` zjistí hned, `POST /update` aktualizuje ccc (přepnutí na vydání, instalace, restart; 409 s důvodem, když to nejde) — viz [Nastavení](settings.md#aktualizace-ccc). Aplikace ukáže banner s tlačítkem *Aktualizovat*; proud událostí hlásí `update`, když se objeví nová verze.

## Aktualizace aplikace

Aplikace se aktualizuje podle verze, ne přes ccc, a z vlastního repozitáře: tag `X.Y.Z` (bez `v`) v [ccc_android](https://github.com/Luquas95/ccc_android) spustí GitHub Actions, které aplikaci sestaví a otestují; správce ji pak podepíše na svém počítači a k vydání přiloží `ccc.apk` (podpisový klíč se na GitHub nedostane). Aplikace se GitHubu ptá na nejnovější vydání toho repozitáře (když přijde do popředí, nejvýš jednou za šest hodin; žádný token se neposílá) a když má nižší verzi, ukáže „K dispozici je nová verze aplikace: X“ s tlačítkem *Stáhnout a nainstalovat*. Stažení jde do instalátoru Androidu, který se nejdřív zeptá — a poprvé také na povolení instalace z aplikace. Sestavení vytvořené ručně má verzi `0.0.0-dev` a nikdy se neptá. Aplikace a ccc mají každé svou verzi; API zůstává kompatibilní, takže starší aplikace funguje i s novějším ccc.

**Co seznam ukazuje** (Připojení → *Seznam konverzací*): stav gitu, úkoly ve frontě a složku, každé zapnuté nebo vypnuté — pamatuje
si to telefon, takže malá obrazovka může ukazovat méně.

## Co smí zařízení (role)

Každé zařízení má roli, aby ukradený telefon nebo token nebyl cestou do
celého počítače:

| Role | Smí |
|---|---|
| `read` | dívat se: každé `GET` (seznamy, konverzace, diffy, proud událostí); bez odkazů, které konverzaci ovládají (`remote_control_url`) |
| `operate` | i řídit existující konverzace: zprávy, schvalování, fronta, úkoly, revize, cíle, workflow |
| `admin` | i zakládat konverzace (v jakékoli složce), psát do terminálu, posílat prompty živým relacím, rutiny, vrátit běh, nastavení a presety, záloha, aktualizace ccc a sestavení appky, správa ostatních zařízení |

Nově spárovaný telefon je `operate`; `ccc api pair --role admin` (nebo
*Změnit roli* v seznamu Zařízení, nebo `ccc api role ID admin`) dá víc, `read`
míň. Zařízení z doby před rolemi zůstávají `admin`. Požadavek nad rámec role
dostane `403`; cesta, kterou pravidla neznají, vyžaduje `admin`. Ať má
zařízení jakoukoli roli, smí se samo přejmenovat a odhlásit. `operate` stále
dovolí telefonu schvalovat použití nástrojů v existující konverzaci — to je
smysl jejího řízení.

## Složky a kontroly oprávnění

Konverzace je Claude Code běžící jako ty v nějaké složce, proto o tom, kde
a jak volně ji smí appka založit, rozhoduje počítač — ne telefon:

- **Složky:** `ccc api roots --add ~/DEV` povolí složku a vše v ní
  (`--remove` zruší, bez volby vypíše). Dokud žádnou nenastavíš, smí appka
  jen složky, ve kterých už ccc konverzace má — nedojde do `~` ani `/` (konverzace kdysi založená v `~` nic dalšího neotevře).
  Kontrola sleduje symlinky. Týká se zakládání konverzace, prompt do živé relace, `GET /sessions`
  a `GET /mcp-servers`; složka mimo dostane `403`.
- **Přeskočit všechny kontroly oprávnění:** z appky se odmítne (`403`), dokud
  nespustíš `ccc api skip-permissions on` (`off` to vrátí). Do té doby je
  výchozí volba v appce *Ptát se na všechno*: schvaluješ v appce.

## Záznamy a poplachy

Kdyby se token někdy zneužil, musíš to vidět a dozvědět se o tom. API
zaznamenává všechno, co něco mění, každé odmítnutí (`401` / `403`)
a každé párování — nikdy dotaz, tělo požadavku ani napsané klávesy, jen
cestu a složku nové konverzace. Přečteš to přes `ccc api log` (`-n`
kolik), v *Zařízení → Aktivita* nebo `GET /audit` (admin). Uchovává se
posledních 5000 záznamů.

Přes oznámení do Telegramu / Discordu ([Nastavení](settings.md)) ti ccc
hned řekne i o neobvyklém: spárovalo se nové zařízení, špatný párovací
kód, požadavek se špatným tokenem, zařízení zkouší víc, než jeho role
dovoluje — každý druh nejvýš jednou za 5 minut.

## Zabezpečení spojení

`ccc api doctor` zkontroluje všechno najednou a řekne, co udělat: API
poslouchá jen na tomto počítači (tailnet k němu vede přes
`tailscale serve`), nic není na veřejném internetu (`tailscale funnel`),
databáze je soukromá (práva 600 ve složce 700 — nastaví si je ccc samo),
kolik zařízení smí všechno, která nebyla vidět 90 dní, omezení a jestli se
k tobě dostanou poplachy. Při nálezu problému skončí s kódem 1. Služba
`ccc-api` také běží zpřísněná (žádné nové oprávnění, jen TCP a unixové
sokety, soubory soukromé jen pro tebe).

**Tailscale ACL** — ve výchozím stavu se všechna zařízení tailnetu vidí
navzájem. Označ počítač a telefon tagy a nech k HTTPS portu API jen telefon
(Tailscale admin konzole → Access controls; tagy nastav tam nebo přes
`tailscale up --advertise-tags=tag:ccc-server`):

```json
{
  "tagOwners": {
    "tag:ccc-server": ["autogroup:admin"],
    "tag:ccc-phone":  ["autogroup:admin"]
  },
  "acls": [
    {"action": "accept", "src": ["tag:ccc-phone"], "dst": ["tag:ccc-server:443"]}
  ]
}
```

Své ostatní pravidla nech vedle (příklad nahrazuje výchozí „povol
vše“, takže přidej, co ještě potřebuješ). Vyplatí se i schvalování zařízení
a krátká platnost klíče telefonu a *Tailnet Lock*.

## Endpointy

Každá cesta začíná `/api/v1`; každý požadavek má hlavičku
`Authorization: Bearer <token>`. Odpovědi jsou JSON; chyba je
`{"error": "…"}`. Texty, které ccc posílá (chyby, popisky, volby
nastavení), přicházejí v jazyce hlavičky `Accept-Language` — aplikace
posílá vlastní jazyk (Nastavení → Obecné, také v Připojení; ve výchozím stavu telefonu); česky nebo anglicky, jiný jazyk dostane
angličtinu. Bez hlavičky (starší aplikace) se řídí jazykem samotného
ccc, nastaveným v TUI.
Tento jazyk a vzhled TUI patří počítači: aplikace je neukazuje ani
nemění (má vlastní nastavení jazyka a vzhled má pevný); v nastavení ukazuje
jen to, co platí pro obě strany.

| | |
|---|---|
| `GET /status` | běží daemon?, kolik konverzací, kolik běží, kolik čeká na schválení |
| `GET /conversations` | seznam v pořadí jako v TUI, se skupinami, stavy, zprávami ve frontě a značkami, a stavem gitu (`git`: větev, přidané, odebrané, před, za; `null`, kde Claude nesmí spouštět příkazy) a její poslední [smyčkou k cíli](goal.md) (`goal`: id, status, status_label, phase_label, round, max_rounds, finished_at; za běhu a den po skončení, jinak `null`); `?archived=1` archiv |
| `GET /conversations/{id}` | jedna konverzace s jejími zprávami ve frontě a čekajícími schváleními |
| `GET /conversations/{id}/messages` | její přepis, každá zpráva s `index`; `?after=N` jen novější, `?before=N` starší, `?limit=` kolik (200) |
| `POST /conversations/{id}/messages` | `{"text": "…"}` zařadí zprávu do fronty, jako pole pro zprávu |
| `POST /conversations/{id}/retry` | zopakovat / obnovit zablokovanou nebo pozastavenou konverzaci (`r`) |
| `POST /conversations/{id}/seen` | označit jako přečtenou |
| `POST /conversations/{id}/archive` | `{"archived": true \| false}` — do [Archivu](archive.md) / zpět |
| `DELETE /conversations/{id}` | smazat ji z ccc (ne, když na ní Claude pracuje); přepis Claude Code zůstane |
| `POST /conversations/{id}/group` | `{"name": "…"}` do vlastní skupiny (vznikne, je-li nová), `null` ze skupiny ven |
| `GET /conversations/{id}/export` | `{"filename", "markdown"}` — konverzace jako Markdown, tajné údaje zamaskované |
| `GET /conversations/{id}/files?q=` | soubory její složky pro @zmínku, nejlepší první |
| `POST /conversations` | založit konverzaci: `{"path", "name", "options": [klíče], "interactive", "remote_control", "auto_continue", "worktree", "session_id", "pr", "prompt"}` — jako v [Zahájení konverzace](new-conversation.md); vynechané volby jsou výchozí |
| `GET /conversations/{id}/tasks` | její [fronta úkolů](tasks.md) a oba přepínače; `POST` přidá `{"text", "when"}` — when je prázdné (co nejdřív), `HH:MM`, `RRRR-MM-DD HH:MM` nebo `reset` |
| `POST /tasks/{id}` | `{"text"}` / `{"when"}` úkolu, který ještě nezačal; `DELETE` ho odebere; `POST /tasks/{id}/move` `{"delta": -1 \| 1}` |
| `POST /conversations/{id}/switches` | `{"paused": bool}` nespouštět další úkoly, `{"auto_continue": bool}` po limitu využití |
| `GET /conversations/{id}/runs` | její běhy, nejnovější první: stav, tokeny, [commit](git.md) |
| `GET /runs/{id}/diff` | commit běhu po řádcích (druh, soubor, čísla řádků, jde komentovat) |
| `POST /runs/{id}/comments` | `{"comments": [{"index", "text"}]}` k řádkům diffu, poslané Claudovi jako jeden úkol |
| `POST /runs/{id}/revert` | vrátí commit běhu novým commitem; `{"tell": true}` k tomu zařadí poznámku pro Clauda |
| `GET /conversations/{id}/reviews` | její [recenze](review.md), nejnovější první; `POST` o jednu požádá `{"instructions", "until_pass", "rounds"}`; `POST …/reviews/stop`; `POST /reviews/{id}/apply` pošle nálezy recenze FAIL / PARTIAL zpět jako úkol |
| `GET /conversations/{id}/goals` | její [smyčky k cíli](goal.md); `POST` jednu spustí `{"goal", "check", "rounds"}`; `POST …/goals/stop` |
| `GET /workflow-templates` | šablony [workflow](workflows.md) s jejich kroky |
| `GET /conversations/{id}/workflows` | její běhy workflow, každý s kroky a tím, co řekl každý agent; `POST` jeden spustí `{"template", "task", "check", "rounds"}`; `POST …/workflows/answer` `{"approve", "note"}` u schvalovacího kroku; `POST …/workflows/stop` |
| `GET /routines` | všechny [rutiny](routines.md) a druhy plánu; `POST` přidá `{"conversation_id", "name", "prompt", "kind", "value", "fresh"}`; `POST /routines/{id}` ji změní, `DELETE` smaže; `POST …/enabled` `{"enabled"}`, `POST …/run` |
| `GET /live-sessions` | [relace Claude Code](live-sessions.md) běžící na počítači (`kind`, `status`, `state` tak, jak je pojmenoval Claude Code, a `kind_label`, `status_label`, `state_label` v jazyce požadavku); `POST /live-sessions/{id}/prompt` `{"prompt"}` |
| `GET /analytics?year=&month=` | tokeny měsíce po dnech, modelech a konverzacích, měsíce s daty ([Analytika](analytics.md)); `POST /analytics/archive` archivuje skončené měsíce hned |
| `GET /settings` | [nastavení](settings.md) ccc s možnostmi u každého (tajné údaje jen jako nastaveno / nenastaveno); `POST` některá změní; `POST /settings/telegram-test`, `telegram-off`, `discord-test`, `discord-off` |
| `POST /prompt-presets` | `{"presets": [{"name", "text", "send_now"}]}` — všechny, v pořadí; `POST /launch-presets` uloží předvolbu nové konverzace, `POST /launch-presets/delete` `{"name"}` |
| `GET /conversations/{id}/terminal` | co teď ukazuje Claude Code interaktivní konverzace — obrazovka s barvami (ANSI) a kurzorem |
| `POST /conversations/{id}/terminal` | do jejího Claude Code: `{"paste", "submit"}` text jako jedno vložení (potom Enter, pokud `submit` není false), `{"keys": […]}` klávesy (Enter, Escape, Tab, BTab, šipky, Home, End, PageUp, PageDown, BSpace, Space, C-c, C-d, C-l, C-o, C-r, C-t, C-u), `{"type": "y"}` pár znaků |
| `POST /backup` | zálohovat databázi hned |
| `GET /launch-options` | volby, se kterými jde konverzace založit (klíče a popisky) |
| `GET /projects` | naposledy použité složky |
| `GET /plugins` | `{installed, enabled}`: id nainstalovaných pluginů Claude Code a těch zapnutých ve výchozím stavu (zaškrtnou se jako `plugin:<id>` v `options`, spolu s `plugins_select`) |
| `GET /mcp-servers?path=` | názvy MCP serverů nastavených pro složku (zaškrtnou se jako `mcp:<název>` v `options`, spolu s `mcp_select`) |
| `GET /sessions?path=` | relace Claude Code ve složce, nejnovější první (k pokračování: `session_id` v `POST /conversations` se `session_resume`) |
| `GET /launch-presets` | předvolby okna Nová konverzace a poslední volba |
| `GET /prompt-presets` | přednastavené prompty (Alt+1 … 9 v ccc) |
| `POST /pair` | `{"code", "name"}` — bez tokenu: vymění párovací kód z QR za nové zařízení; vrátí `{"token", "device_id", "name", "version"}`, 401 při špatném, použitém nebo prošlém kódu |
| `GET /devices` | přihlášená zařízení: `role`, jméno, vytvoření, naposledy vidět, adresa; `current` označí volajícího. `POST /devices/{id}` `{"name"}` jedno přejmenuje, `DELETE /devices/{id}` ho hned odhlásí (i otevřená spojení; řádek zůstane jako historie). `GET /devices?all=1` přidá odhlášená s `revoked_at` a `revoked_by` (`app`, `computer`, `token`) |
| `GET /groups` | seskupení a tvé vlastní skupiny; `POST /groups/rename` `{"old", "new"}`, `POST /groups/delete` `{"name"}` |
| `GET /search?q=` | hledání ve všech konverzacích (i archivovaných), nejnovější první |
| `GET /waiting` | konverzace, které tě potřebují, nejnaléhavější první (Alt+N v ccc) |
| `GET /permissions` | všechna čekající [schválení](approvals.md) |
| `POST /permissions/{id}` | `{"decision": "allow" \| "always" \| "deny"}` |
| `GET /usage` | [limity využití](limits.md) (null, když nejsou známé) |
| `GET /events` | Server-Sent Events: `changed` s `{"conversations": [id], "removed": [id]}`, kdykoli se nějaká změní |
