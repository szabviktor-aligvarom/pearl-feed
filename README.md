# pearl.de ár- és készletfeed

Napi szinten frissülő ár- és készletfeed a [pearl.de](https://www.pearl.de) kínálatáról,
állandó linken, JSON és CSV formátumban. A termékek azonosító kulcsa a **cikkszám**
(Artikelnummer / SKU), ami garantáltan egyedi a feedben.

## Állandó linkek

Ezek a linkek frissítés után sem változnak:

| Formátum | Link |
|---|---|
| JSON | https://raw.githubusercontent.com/szabviktor-aligvarom/pearl-feed/main/feed/pearl_feed.json |
| CSV | https://raw.githubusercontent.com/szabviktor-aligvarom/pearl-feed/main/feed/pearl_feed.csv |
| Állapot (heartbeat) | https://raw.githubusercontent.com/szabviktor-aligvarom/pearl-feed/main/feed/heartbeat.json |

A commit-történet ingyenes árnaplóként működik: minden frissítés külön commit,
így visszakereshető, mikor mennyi volt egy termék ára.

## Fontos tudnivalók az adatról

- **Az árak bruttó, ÁFÁ-val terhelt kiskereskedelmi (végfelhasználói) árak, EUR-ban.**
  Ezek NEM nettó beszállítói árak. A szállítási költség nincs benne (a pearl.de
  jellemzően 1,99 EUR-tól számol szállítást).
- **A készlet csak van/nincs, darabszám nem elérhető.** A forrás strukturált adata
  csak `InStock` / `OutOfStock` állapotot közöl. A `keszlet_tipus` mező ezért mindig
  `van_nincs`.
- **A pearl.de folyamatosan futtat akciókat**, ezért az árak egy része átmeneti.
  Az `akcios`, `listaar` és `kedvezmeny_szazalek` mezők jelzik, ha egy termék
  akciós áron van. Az akciós árak bármikor visszaállhatnak a listaárra.
- A `suly` mező "best effort": csak akkor van kitöltve, ha a termékleírás tartalmazza
  (kg-ban normalizálva). A forrás nem közöl külön szállítási súlyt.

## A feed mezői

| Mező | Leírás |
|---|---|
| `cikkszam` | Artikelnummer / SKU. **Az azonosító kulcs, egyedi.** |
| `nev` | Terméknév |
| `marka` | Márka |
| `aktualis_ar` | Aktuális bruttó ár |
| `listaar` | Áthúzott / listaár, ha van akció (különben üres) |
| `akcios` | true / false |
| `kedvezmeny_szazalek` | Kedvezmény százalékban |
| `keszleten` | true = van, false = nincs, üres = ismeretlen |
| `keszlet_tipus` | Mindig `van_nincs` (nincs darabszám) |
| `suly` | Súly kg-ban, ha kinyerhető |
| `gtin13` | EAN / GTIN-13 |
| `termek_url` | Termékoldal URL |
| `fokep_url` | Főkép URL |
| `penznem` | EUR |
| `volt_duplikalt` | true, ha a cikkszám többször szerepelt a forrásban |
| `duplikatum_darab` | Hányszor szerepelt |
| `utolso_modositas` | A feed generálásának időpontja |

A pénznem a feed fejlécében (`feed_info.penznem`) és minden terméksoron is szerepel.

### Duplikált cikkszámok kezelése

Ha ugyanaz a cikkszám többször szerepel a forrásban, a feed a **készleten lévő
változatot** tartja meg (másodlagos szempont: legyen ára, legyen neve).
A `volt_duplikalt` mező jelzi az ütközést, a `duplikatum_darab` pedig a darabszámot.
A feedben a cikkszám a végén garantáltan egyedi.

## Frissítés

A feed **3 naponta 22:00-kor** (Europe/Budapest) frissül, egy fix IP-vel rendelkező
Windows gépen futó ütemezett feladat által. A fix IP azért kell, mert a pearl.de
nem szolgálja ki a felhő-szolgáltatók (Google Cloud, Azure, AWS) IP-címeit, így
GitHub Actions futóról nem érhető el.

### Csendes hiba elleni védelem

A script hibával leáll, és **nem írja felül a jó adatot**, ha:

1. a termékszám a beállított minimum (`PEARL_MIN_PRODUCTS`, alap: 12000) alá esik
2. a termékek több mint 20%-ánál nincs ár (`PEARL_MAX_MISSING_PRICE`)
3. a katalógus 30%-nál nagyobbat zuhan az előző futáshoz képest (`PEARL_MAX_SHRINK`)

Ilyenkor az utolsó jó feed marad kint a linken, tehát a shop soha nem kap hibás adatot.

Van egy vészleállító is: ha a forrás sok `403` / `429` választ ad (IP-tiltás jele),
a futás azonnal leáll, hogy ne rontsuk el a hozzáférést.

### Gépleállás figyelése

A `watchdog` GitHub Action naponta 09:00-kor ellenőrzi a `feed/heartbeat.json` fájlt.
Ha az utolsó sikeres futás 4 napnál régebbi, **emailt küld**, hogy a gépet újra
be kell kapcsolni. Ez a figyelő a GitHubon fut, nem a Windows gépen, ezért akkor is
működik, ha a gép teljesen le van állva.

## Telepítés a Windows gépre

1. Telepíts [Pythont](https://www.python.org/downloads/) (3.9 vagy újabb) és
   [Gitet](https://git-scm.com/download/win). A Python telepítőnél pipáld be az
   "Add Python to PATH" opciót.

2. Klónozd a repót a `C:\pearl-feed` mappába:

   ```
   git clone https://github.com/szabviktor-aligvarom/pearl-feed.git C:\pearl-feed
   ```

3. Állítsd be az email értesítéshez a környezeti változókat (egyszer kell):

   ```
   setx PEARL_MAIL_FROM "sajat@gmail.com"
   setx PEARL_MAIL_TO "sajat@gmail.com"
   setx PEARL_MAIL_PASS "gmail app password"
   ```

   A Gmail app password itt kérhető: https://myaccount.google.com/apppasswords

4. Vedd fel az ütemezett feladatot (Rendszergazdaként futtatott PowerShellben):

   ```powershell
   cd C:\pearl-feed
   .\install_task.ps1
   ```

   Ez létrehoz egy "Pearl feed frissites" nevű feladatot, ami 3 naponta 22:00-kor fut,
   és a kihagyott futást a gép bekapcsolása után pótolja.

5. Próbafutás kézzel:

   ```powershell
   cd C:\pearl-feed
   python build_feed.py --limit 50
   ```

## Kézi futtatás

```
python build_feed.py                # teljes katalógus
python build_feed.py --limit 200    # csak az első 200 termék (teszt)
python build_feed.py --workers 2    # kíméletesebb tempó
```

## Adatforrás

A feed a pearl.de `sitemapindex.xml` alapján gyűjti a termék URL-eket, majd
minden termékoldalról a `schema.org/Product` JSON-LD blokkot olvassa ki. Ez a blokk
a Google Shopping miatt van kitéve, ezért stabilabb, mint a HTML elrendezésre
épülő megoldások: egy dizájnváltás nem törik el.

A letöltés streamelve történik és megszakad, amint a szükséges adat megvan
(termékenként ~150-250 KB helyett a teljes ~900 KB helyett), így a forgalom
jelentősen csökken.
