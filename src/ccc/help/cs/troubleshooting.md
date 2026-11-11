# Řešení potíží a jak to funguje

## Postranní panel ukazuje „daemon neběží“

Bez daemonu se nic nestane. Zkontroluj ho a spusť:

```bash
systemctl --user status ccc        # běží?
systemctl --user restart ccc
journalctl --user -u ccc -f        # jeho log
```

Aby běžel i po odhlášení nebo zavření SSH, jednou zapni lingering:
`loginctl enable-linger "$USER"`.

**Šifrovaný domovský adresář (ecryptfs):** služby nemůžou naběhnout při
startu počítače, dokud se nepřihlásíš, protože jejich soubory jsou ještě
zašifrované. Spouštěj je radši po přihlášení, třeba položkou automatického
spuštění ve svém desktopu, která zavolá
`systemctl --user daemon-reload && systemctl --user start ccc.service ccc-tui.service`.

## `ccc` se neotevře / ukazuje něco divného

Stále běžící ccc má vlastní tmux server (socket
`/tmp/ccc-tmux-<uid>/ccc`), takže ho v obyčejném `tmux ls` neuvidíš.
`ccc` se k němu připojí; pokud neběží služba `ccc-tui`, `ccc` ji spustí.
Když ccc samo skončí, za vteřinu je zpátky.
`systemctl --user restart ccc-tui` ho restartuje.

## ccc a tvůj vlastní tmux

Tmux servery ccc jsou oddělené od tvých: jejich sockety jsou
v `/tmp/ccc-tmux-<uid>/` (ne vedle tvých v `/tmp/tmux-<uid>/`) a nikdy
nenačítají tvůj `~/.tmux.conf`, takže změna tvého nastavení tmuxu ccc
nezmění. Dvě proměnné prostředí je oddělí ještě víc:

- `CCC_TMUX_BIN` — program tmux, který ccc používá (např. vlastní build,
  aby upgrade systémového tmuxu ccc neovlivnil);
- `CCC_TMUX_DIR` — adresář pro sockety ccc.

Nastav je pro služby (např. v `~/.config/environment.d/ccc.conf`, pak se
znovu přihlas nebo `systemctl --user daemon-reload` a restartuj
`ccc ccc-tui`) **a** v shellu, ze kterého spouštíš `ccc` — obojí musí
používat stejný tmux.

Když spustíš `ccc` uvnitř svého tmuxu, vnoří se: klávesy a myš nejdřív
vidí tvůj tmux (jeho prefix, režim myši, copy mode, nastavení schránky).

## Konverzace se zastavila

`✖` = zastavená chybou, `‖` = čeká na limit využití nebo je pozastavená.
Viz [Limity využití a zablokované fronty](limits.md).

## Obnovení zálohy

1. `systemctl --user stop ccc ccc-tui`
2. Zkopíruj zálohu z `~/.local/share/ccc/backups/` přes
   `~/.local/share/ccc/history.db`.
3. `systemctl --user start ccc ccc-tui`

## Claude Code ve vlastním rámu ccc (experimentální)

Ve výchozím stavu se Claude Code otevírá v panelu tmuxu vedle ccc, takže
čáry kolem něj kreslí tmux (rovné rohy, titulek odsazený o 2 sloupce).
S `CCC_EMBEDDED_TERMINAL=1` v prostředí `ccc-tui` ukazuje Claude Code
samo ccc: zaoblený rám jako u menu, s titulkem, konverzacemi, které na
tebe čekají, značkou **⌨ typing here** a předvolbami pod ním. tmux zůstává
jen jako okno, ve kterém ccc žije (běží dál a můžeš se k němu připojit
odkudkoli), a jako hostitel sezení konverzací, takže se nic nemění pro
daemon, API ani aplikaci v telefonu. Klávesy jsou stejné: Alt+0 přepíná
klávesnici mezi seznamem a Claude Code, Ctrl+] rám zavře (Claude běží
dál). Tažením myší vybereš text — po pustění jde do schránky; kolečko
posouvá historii Clauda.

Zapnutí: `systemctl --user edit ccc-tui`, přidej `[Service]` /
`Environment=CCC_EMBEDDED_TERMINAL=1` a pak
`systemctl --user restart ccc-tui`. Odebráním řádku se vrátíš zpět.

## Jak ccc funguje

- **`ccc-daemon`** je jediný, kdo kdy spouští Clauda. Je to služba na
  pozadí, která přežije odhlášení ze SSH, pády (restartuje se)
  i restarty počítače.
- **TUI** jen čte a zapisuje databázi ccc; nikdy nespouští prompt. Můžeš
  ho kdykoli zavřít.
- Obojí sdílí jednu databázi SQLite (`~/.local/share/ccc/history.db`);
  úkol se přebírá atomicky, takže nikdy neběží dvakrát.
- Konverzace s Claude Code vedle ccc běží jako interaktivní procesy
  `claude` ve skrytém tmux serveru (socket `/tmp/ccc-tmux-<uid>/ccc-sessions`), které daemon
  drží naživu.
- ccc používá tvoje běžné přihlášení do Claude Code — žádný API klíč —
  a **stejné** 5hodinové / týdenní využití jako každá jiná relace Claude
  Code na počítači. Zobrazené ceny jsou ekvivalent ceníku Anthropicu, ne
  poplatek navíc.
- Limit využití se pozná podle vlastního chybového stavu Claude Code
  (429), ne hádáním z textu. Když čas obnovení není známý, ccc to zkusí
  znovu za 15, pak za 30 a pak za 60 minut.
- Jazyk (Nastavení → Vzhled, nebo `ccc config language`) platí pro TUI,
  nápovědu, notifikace, poznámky měsíčního archivu i výpisy `ccc`.
