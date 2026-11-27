# Poznámky

ccc ukládá **poznámky** jako obyčejné soubory Markdown, takže je můžeš psát
kdekoli a nic jiného než ccc nepotřebuješ. Otevřeš je klávesou `N` (Shift+N):
nahoře seznam poznámek, pod ním vykreslená ta vybraná.

Soubory leží v `~/.local/share/ccc/notes/` (vedle databáze ccc) — otevřeš je
v jakémkoli editoru, zazálohuješ nebo dáš složku pod git. Název může začínat
složkou: `napady/api` je soubor `napady/api.md`.

[Android aplikace](android-api.md) čte a zapisuje tytéž poznámky, takže co
napíšeš v telefonu, je tady za okamžik, a naopak.

## Klávesy

| Klávesa | |
|---|---|
| `+` | napsat novou poznámku: ccc se zeptá na název a otevře editor |
| `e` / `Enter` | upravit vybranou poznámku v editoru |
| `r` | přejmenovat poznámku nebo ji přesunout do jiné složky (`prace/napady`) |
| `Delete` | smazat poznámku — soubor se přesune do `deleted-notes/` vedle poznámek, nemaže se |
| `/` | hledat v názvech i textu poznámek (`Esc` hledání zruší) |
| `Esc` | zpět |

Poznámky nejsou součástí zálohy databáze ccc (Nastavení → záloha ukládá
jen `history.db`): složku `notes/` si zálohuj sám, třeba přes git.

## Editor

Během editoru ccc ustoupí a převezme, co uložíš. Který editor to je, vybereš
v [Nastavení](settings.md) (Auto: `$VISUAL` / `$EDITOR`, jinak první
nainstalovaný, např. nano).

## Úprava z obou stran

Změní-li se táž poznámka, zatímco ji upravuješ (třeba ji uložíš v telefonu),
ccc ji nepřepíše: tvoje verze se uloží vedle jako `název (conflict).md`
a obě si spojíš, jak chceš.
