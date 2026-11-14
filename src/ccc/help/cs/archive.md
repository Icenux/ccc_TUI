# Archiv

Konverzace, se kterými jsi skončil(a), patří do Archivu (`h`) — nepletou
se, ale nic se neztratí. Když je Archiv prázdný, stojí v něm **PRÁZDNO**.

![Archiv](../../../../docs/screenshots/archive.png)

## Archivace

`x` v [Konverzacích](conversations.md) archivuje vybranou konverzaci.
Zmizí z Konverzací i z Přehledu a objeví se v Archivu.

Archivovaná konverzace je zmrazená, ať je na pozadí nebo ne: nic z její
fronty neběží a nespouští se žádný proces.

- Konverzace s **Claude Code vedle ccc**: její proces Claude Code skončí
  (uvolní paměť) a úkoly ve frontě čekají.
- Konverzace **na pozadí** přestane zpracovávat svou frontu; čekající
  úkoly zůstanou, dokud ji neobnovíš.
- Přidat do ní úkol nebo poslat zprávu nejde (API odpoví 409) — nejdřív
  ji obnov.

## Obnovení

V Archivu:

| Klávesa | |
|---|---|
| `x` | obnoví konverzaci — vrátí se do Konverzací |
| `Enter` | obnoví ji a otevře v Konverzacích |
| `Delete` | smaže ji z ccc (nejdřív se zeptá); na nadpisu všechny konverzace té skupiny nebo složky projektu |
| `/` | hledá — nejdřív v Archivu; v okně přepneš na Konverzace nebo všude |
| `f` | [filtruje seznam](conversations.md#filtr-seznamu); `Enter` ukáže první výsledek (neobnoví ho) |
| `m` | [skupiny](conversations.md#skupiny) jako v Konverzacích; `Enter` na nadpisu ho sbalí (nic neobnoví) |
| `e` | exportuje ji jako Markdown |

Obnovená konverzace pokračuje tam, kde byla: Claude Code se znovu spustí
s celou historií (`--resume`) a čekající úkoly jdou dál.

## Mazání

Konverzaci jde smazat jen z Archivu — nejdřív ji archivuj. `Delete` na
nadpisu smaže celou skupinu (nebo složku projektu) se všemi jejími
konverzacemi. ccc se před smazáním zeptá. Co zmizí a co zůstane:

- **Zmizí:** konverzace v ccc, její úkoly ve frontě a texty jejích běhů.
- **Zůstane:** její počty tokenů v [Analytice](analytics.md) (jako
  *smazaná*) a přepis v samotném Claude Code — relaci si pořád můžeš
  vybrat přes *Obnovit vybranou…* při
  [zahájení konverzace](new-conversation.md).

Konverzaci, na které Claude pracuje, smazat nejde, dokud nedoběhne nebo
ji nepozastavíš (`t`, `p`).
