# Schvalování nástrojů

Claude Code se ptá, než použije nástroj — spustí příkaz, upraví soubor —
pokud jsi mu to předem nepovolil(a). Kde odpovídáš, záleží na konverzaci:

- **Claude Code vedle ccc:** Claude se ptá přímo ve svém panelu jako
  v každém terminálu. Odpověz tam. (Konverzacím se vzdáleným ovládáním
  můžeš odpovídat i z telefonu.)
- **Konverzace na pozadí** založené s *Accept edits*, *Manual*
  nebo *Plan*: není tu terminál, kde by se ptal, takže se zeptá ccc.
  Claudova žádost čeká — bez časového limitu — dokud nerozhodneš.
- *Bypass permissions*: nic se neptá, Claude dělá
  všechno.

## Odpověď v ccc

Konverzace, která na tebe čeká, má v seznamech `?` a *Čeká na tvé
schválení: Bash — stiskni p*. V [Konverzacích](conversations.md) stiskni
`p`:

![Schválení nástroje](../../../../docs/screenshots/approval.png)

| Klávesa | |
|---|---|
| `y` | povolí jednou |
| `a` | vždy povolí tento nástroj v této konverzaci |
| `n` | zamítne — Claude se to dozví a poradí si jinak |
| `Esc` | rozhodneš později |

Pokud máš nastavený Telegram nebo Discord ([Nastavení](settings.md)),
zpráva ti řekne, že čeká žádost.
