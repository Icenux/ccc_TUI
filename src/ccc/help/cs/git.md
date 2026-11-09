# Git: automatické commity

V git repozitáři ccc commitne výsledek každého úspěšného úkolu **na
pozadí**, takže každý úkol je jeden commit, který si můžeš prohlédnout
nebo vrátit:

- zpráva `ccc: <první řádek úkolu>` s trailerem
  `ccc-conversation: <název>`;
- `ccc runs KONVERZACE` vypíše commit každého běhu (viz
  [Příkazová řádka](command-line.md)).

## Vrácení běhu

`ccc revert BĚH` vrátí commit běhu **novým commitem** (`git revert`).
Nic se nepřepisuje a i vrácení jde vrátit. Běh pak má v `ccc runs`
u commitu `↺`. `--tell` k tomu zařadí Claudovi krátkou poznámku ve
frontě konverzace; jinak může jeho další úkol se změnami pořád počítat.

Funguje, když:

- konverzace zrovna nic nedělá (žádný úkol neběží ani nečeká) — úkol,
  který by soubory upravoval, by své změny přimíchal do vrácení;
- ve složce nejsou necommitnuté změny a commit je v historii větve;
- konverzace byla spuštěná s **Přeskočit všechny kontroly oprávnění**
  (stejně jako samotné commity, viz níže).

Vrácení, které se pohádá s pozdějšími změnami, se zruší: nic se nezmění
a ccc to řekne. `ccc runs KONVERZACE` vypíše běhy s jejich id.

## Více konverzací v jedné složce

Dvě konverzace, které upravují stejné soubory zároveň, si překážejí a
commit po úkolu jedné by mohl zahrnout rozdělané změny druhé. Proto
v jednom git pracovním stromu:

- **úkoly na pozadí se střídají**: úkol počká, dokud jiná konverzace ve
  stejné složce dokončí svůj (Konverzace ukážou kterou);
- ccc **necommitne**, když ve složce pracuje jiná konverzace (i
  interaktivní, která zrovna zpracovává, co jsi napsal) — `ccc runs`
  to uvede;
- Konverzace ukazují řádek ⚠ s ostatními, které složku sdílejí.

Aby mohly opravdu pracovat **vedle sebe**, dej konverzaci při
[spuštění](new-conversation.md) **vlastní git worktree**: samostatnou
pracovní kopii repozitáře na vlastní větvi (`worktree-<název>`, ve
`.claude/worktrees/<název>` — tam, kde si je drží `claude --worktree
<název>`, mimo `git status` hlavní kopie). ccc tam commituje její úkoly;
větev slouč, až budeš spokojený. Nepotřebný worktree odstraníš přes
`git worktree remove .claude/worktrees/<název>`.

Konverzace ukazují, jak je na tom každá v gitu — v seznamu za názvem
a v záhlaví: `+12−3` změněné a ještě necommitnuté řádky, `↑2` commity
navíc oproti základu a `↓1` chybějící (základ je upstream větve, u
worktree větev hlavní kopie, ze které vznikl). Když je vše commitnuté
a srovnané, nic se neukazuje. Jako všechno s gitem v ccc jen
u konverzací, které smí spouštět příkazy; obnovuje se každých 15 sekund
a nové soubory, které git ještě nesleduje, se nepočítají.

Nová pracovní kopie má jen soubory, které git sleduje, takže by chyběl
`.env`, lokální certifikáty nebo konfigurace, bez kterých projekt
nepoběží. Vypiš je do souboru **`.worktreeinclude`** v kořeni
repozitáře (syntaxe gitignore — stejný soubor čte Claude Code, Conductor
i Codex), např.:

```
.env
config/local.json
```

ccc do každého nového worktree zkopíruje soubory, které odpovídají vzoru
**a zároveň** je git ignoruje — nikdy sledovaný soubor, nikdy přes
symbolický odkaz, nikdy přes existující soubor, nejvýš 500 souborů po
20 MB.

## Kdy ccc necommitne

`ccc runs` řekne proč:

- ve složce už před úkolem byly **necommitnuté změny** — to je tvoje
  práce, ccc ji nikdy do svého commitu nepřimíchá;
- nic se nezměnilo, nebo je repozitář na odpojeném HEAD;
- konverzace nebyla založená s **Přeskočit všechny kontroly oprávnění**.
  ccc spouští git sám, mimo režim oprávnění Clauda, takže v konverzaci
  s *Automaticky přijímat úpravy* / *Jen plán* by tam Claude jinak mohl
  podstrčit git hook, který by ccc spustil;
- config repozitáře používá filtry, includy nebo podepisování (nebo se
  během úkolu změnil) — z bezpečnosti tam pak ccc git vůbec nespouští.

Konverzace s Claude Code vedle ccc ccc necommituje — tam commituje
Claude sám, když ho o to požádáš.
