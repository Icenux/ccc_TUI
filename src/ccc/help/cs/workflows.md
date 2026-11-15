# Workflow

**Workflow** pošle úkol konverzace řetězcem specialistů: plánovač, tvoje
schválení, stavitel (samotná konverzace), kontrola, několik nezávislých
recenzentů najednou a rozhodčí, který jejich nálezy porovná. Když práce
neprojde, vrátí se staviteli s tím, co se našlo — omezený počet krát.

Klávesa `w` v [Konverzacích](conversations.md) nebo v detailu fronty
otevře okno **Workflow** vybrané konverzace.

## Spuštění

1. Vyber **šablonu**. Pod ní uvidíš její kroky, např.
   `plán → tvoje schválení → stavba → kontrola → recenze ×2 → rozhodčí`.
2. Napiš **úkol**.
3. Volitelně **kontrolní příkaz** pro její krok kontroly (`pytest -q`) a
   kolikrát se smí vrátit ke stavbě (**kola**).
4. **Spustit** (`Ctrl+S`). Kroky spouští daemon; okno ukazuje každý,
   jak probíhá (✓ hotovo, ▶ teď, ⏸ čeká na tebe, ○ přijde) a co který
   agent řekl.

## Kroky

| Krok | Co se děje |
|---|---|
| plán | plánovač přečte kód a napíše plán s **kritérii přijetí**, podle kterých se práce posoudí |
| tvoje schválení | běh čeká: přečti plán, **Schválit** (s volitelnou poznámkou pro další krok) nebo **Zamítnout** (běh skončí). Dostaneš upozornění na Telegram / Discord |
| stavba | úkol (s plánem, v dalších kolech s nálezy) přijde do [fronty úkolů](tasks.md) konverzace |
| kontrola | ccc spustí kontrolní příkaz; selhání jde rovnou zpět ke stavbě. Jen v konverzacích s *Bypass permissions*, stejně jako u [smyčky k cíli](goal.md) |
| recenze | 1–4 recenzenti **paralelně**, každý s vlastním zadáním (např. správnost, bezpečnost) a modelem |
| rozhodčí | porovná zprávy recenzentů, v kódu sám ověří, v čem se liší, a dá verdikt |

Recenze bez rozhodčího za sebou projde jen tehdy, když všichni
recenzenti řeknou PASS. Všichni agenti kromě stavitele **jen čtou** —
jako [recenzent](review.md): diff jim připraví ccc, nic nemohou změnit
ani spustit a nenačítají nastavení projektu. Jejich tokeny se počítají
v [Analytice](analytics.md).

Běh skončí **prošlo** po posledním kroku, **neprošlo**, když dojdou kola
nebo ho zamítneš, **chyba**, když selže úkol nebo agent, nebo
**zastaveno** tebou. V jedné konverzaci běží najednou jen jedno
workflow, smyčka k cíli nebo recenze.

## Šablony

Vestavěné:

- **Plán → schválení → stavba → recenze**
- **Stavba → dvě recenze paralelně → rozhodčí** (správnost + bezpečnost)
- **Plán → schválení → stavba → dvě recenze → rozhodčí**

Vlastní jsou soubory JSON, importuješ je přes `ccc workflow import
SOUBOR` (jako výchozí bod si nějakou vyexportuj přes `ccc workflow
export NÁZEV SLOŽKA`). Import nejdřív vypíše všechno, co šablona
agentům říká, i příkaz, který spouští, a uloží ji až s `--yes` — cizí
šablona řídí Clauda v tvém projektu, tak si ji přečti. Kontrolní příkaz
zadaný při spuštění má přednost před příkazem ze šablony.

```json
{
  "name": "pecliva",
  "title": "Plán, stavba, tři recenze",
  "max_rounds": 3,
  "steps": [
    {"type": "plan", "model": "opus"},
    {"type": "gate", "text": "Schválit plán?"},
    {"type": "code", "brief": "Veřejné API nech beze změny."},
    {"type": "check", "command": "pytest -q"},
    {"type": "review", "reviewers": [
      {"name": "spravnost", "brief": "Dělá, co úkol chce? Testy?"},
      {"name": "bezpecnost", "brief": "Validace vstupů, tajemství, oprávnění.", "model": "opus"},
      {"name": "vykon", "brief": "Zbytečné dotazy nebo smyčky na horkých cestách."}
    ]},
    {"type": "arbiter", "model": "opus"}
  ]
}
```

`type` je jedno z `plan`, `gate`, `code`, `check`, `review`, `arbiter`;
`brief`, `model` a `text` jsou volitelné. Rozhodčí jde hned po recenzi;
kontrola, recenze a rozhodčí potřebují před sebou krok stavby.
`max_rounds` je 1–10.

Z příkazové řádky: `ccc workflow` (viz [Příkazová řádka](command-line.md)).
