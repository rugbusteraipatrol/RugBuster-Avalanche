# RugBuster Shield (Windows GUI)

Lokalni Windows 11 endpoint monitor: prati aktivne odlazne TCP konekcije,
povezuje ih sa procesom koji ih je otvorio, i upozorava korisnika kroz
sistemsku traku i toast notifikacije kada nesto izgleda sumnjivo (nov
proces, nepotpisan fajl, nov fajl koji odmah "zove kuci", itd).

Ovo je samostalan alat u `windows-shield/` — odvojen je od ostatka ovog
repoa (Solidity ugovori + Python risk engine za on-chain skeniranje na
Avalanche-u); radi lokalno na Windows masini korisnika i nije povezan sa
blockchain skenerom.

## Fajlovi

- `RugBuster-Shield-GUI.ps1` — glavni WinForms skript.
- `rugbuster_whitelist.txt` — rucno editabilna lista poznatih/bezbednih
  procesa (jedan naziv po liniji, npr. `chrome.exe`). Ovi procesi nikad
  ne generisu MEDIUM/HIGH.
- `assets/rugbuster-shield-logo.png` / `.ico` — logo (vidi ispod).
- `rugbuster_alert_history.json`, `rugbuster_seen_state.json` — generisu
  se automatski pri prvom pokretanju (istorija upozorenja / memorija
  "vidjenih" procesa i destinacija), nisu deo repoa dok se ne pokrene.

## Poreklo brendinga

Boje i fontovi su izvuceni direktno iz `:root` CSS promenljivih i logo
markupa u `rugbusteraipatrol/rugbuster-website` (`index.html`), koji je
ziva rugbuster.io stranica:

| Token | Hex | Upotreba u Shield GUI-u |
|---|---|---|
| `--neon-cyan` | `#00f5ff` | primarni akcenat, "RUG" u logu, cyan granice/headeri |
| `--neon-pink` | `#ff00c8` | sekundarni akcenat, "BUSTER" u logu |
| `--neon-green`| `#00ff88` | severity LOW |
| `--neon-orange`| `#ff6b00` | severity MEDIUM ("WORM") |
| sajtova "danger" crvena | `#ff3d3d` | severity HIGH ("DANGER") — sajt koristi ovu boju (ne neon-pink) za sve opasnost/threat elemente (`.t-danger`, `.port-risk.r-danger`, critical alert modal), pa je Shield GUI prati isto |
| `--dark-bg` | `#050811` | pozadina prozora |
| `--dark-panel`| `#0a0f1e` | header/panel pozadina |
| `--dark-card` | `#0d1527` | pozadina redova/kartica |
| `--text-primary` | `#e8f4ff` | glavni tekst |
| `--text-muted` | `#5a7a9a` | sporedni tekst |

Fontovi na sajtu: **Orbitron** (naslovi/logo), **Rajdhani** (telo teksta),
**Share Tech Mono** (kod/terminal stil) — Google Fonts, ucitani preko
weba na sajtu. Nisu po defaultu instalirani na Windows-u, pa
`New-BrandFont` u skripti pokusava ove familije, i .NET automatski i
"tiho" prelazi na Consolas/Segoe UI ako nisu instalirane — za piksel-tacan
izgled instaliraj ova tri fonta lokalno (besplatni Google Fonts).

Vizuelni stil sajta: ostri/zaseceni uglovi (`clip-path` poligoni, skoro
bez `border-radius`), tanke cyan ivice niske providnosti, neon
"glow" senke, tamna pozadina. `rugbuster-website/index.html` samog sajta
nema image logo fajl (logo tamo je stilizovan tekst "RUG"+"BUSTER"), ali
pravi ilustrovani logo (metalik stit sa cyber-sovom/robotom, crveno oko,
cyan "circuit" linije) postoji kao `docs/favicon.png` (512x512) u vise
drugih repoa u `rugbusteraipatrol` orgu (isti fajl, isti MD5, u
`rugbuster-multichain` i `RugBuster-CIA-Lab`), zajedno sa zvanicnim
16px/32px favikonima u `rugbuster-multichain/docs/assets/`. To je pravi
brand logo i to je ono sto je kopirano u `assets/rugbuster-shield-logo.png`
(256px, za header) i `assets/rugbuster-shield-logo.ico` (16/32px su
byte-za-byte zvanicni fajlovi; 48/256px su generisani box-resample iz
zvanicnog 512px izvora, cist Python `zlib`/`struct` dekoder+enkoder — nije
bilo ImageMagick/Pillow u sandboxu).

## Severity pragovi (KORAK 3)

Detaljno dokumentovano u kodu iznad `Get-ConnectionSeverity` u
`RugBuster-Shield-GUI.ps1`. Ukratko:

- **LOW** (zeleno) — poznat proces na poznatoj destinaciji (ukljucujuci
  sve na whitelisti). Samo log, bez notifikacije.
- **MEDIUM / "WORM"** (narandzasto) — nov proces ILI poznat proces na
  novu destinaciju. Tiha notifikacija.
- **HIGH / "DANGER"** (crveno) — nepotpisan/izmenjen fajl
  (`Get-AuthenticodeSignature` -> `NotSigned`/`HashMismatch`), ili fajl
  izmenjen u poslednja 24h koji odmah pravi eksternu konekciju. Glasna,
  "pinned" notifikacija (BurntToast `-Scenario Alarm`) koja ostaje dok se
  rucno ne potvrdi u tabu "Istorija upozorenja".
  Treci opcioni signal (neuobicajen "burst" podataka ka istoj nepoznatoj
  destinaciji) je implementiran kao best-effort, iskljucen po defaultu
  (`$Config.EnableBandwidthHeuristic`), jer bi per-connection bandwidth
  attribution na svakih 5s realno zahtevao packet-capture drajver da bi
  bio pouzdan — vidi `Test-BandwidthSpike`.

## Toast notifikacije (KORAK 2)

Koristi [BurntToast](https://github.com/Windos/BurntToast) modul
(auto-install pri prvom pokretanju ako fali, `Install-Module BurntToast
-Scope CurrentUser`). Ako instalacija ne uspe (nema interneta / nema
pristupa PSGallery-ju), automatski pada nazad na klasican
`NotifyIcon.ShowBalloonTip`. Windows 11 sam pozicionira toast
notifikacije u donji desni ugao (Action Center flyout) — to je OS
ponasanje, ne nesto sto skripta kontrolise. Dugme "Detalji" (i na
MEDIUM/HIGH dodatno "Lazna uzbuna" / "Istrazeno - OK") koristi
`ActivationType Protocol` sa custom `rugbuster-shield:` URI semom
(registrovana u `HKCU:\Software\Classes`, bez potrebe za admin
pravima) koja budi vec pokrenutu instancu preko named pipe-a i otvara
glavni prozor na tom redu, ili — ako instanca nije pokrenuta — pokrece
novu i primenjuje akciju cim se ucita.

## Status testiranja uzivo

Skripta je prvobitno pisana i pregledana u Linux sandbox okruzenju bez
Windows runtime-a, pa nije mogla biti pokrenuta ili klik-testirana
end-to-end tamo. Od tada je pokrenuta i testirana uzivo na pravoj
Windows 11 masini (`powershell.exe -ExecutionPolicy Bypass -File
.\RugBuster-Shield-GUI.ps1`, obican korisnicki nalog, bez admin prava).
Tom prilikom su nadjeni i ispravljeni sledeci bugovi:

1. `New-Object System.Drawing.Point(64 + $lblRug.PreferredWidth + 2, 20)`
   je bacao `MethodNotFound`/`op_Addition` gresku — PowerShell-ov
   `New-Object Type(args)` shorthand ne podrzava izraze (samo bare
   literale/promenljive) unutar zagrada. Popravljeno racunanjem X
   koordinate u posebnu promenljivu pre poziva.
2. `DataGridView.GridColor` ne prihvata providnu boju
   (`rgba(0,245,255,.16)` sa alfa=40) — baca izuzetak. Dodat opaque
   `$Brand.GridLine` (rucno alpha-blendovan preko `--dark-bg`) za grid
   linije.
3. `New-BurntToastNotification` u BurntToast 1.1.0 (verzija instalirana
   sa PSGallery-ja) nema `-Action` parametar (samo `-Button`, direktno
   niz dugmadi) niti `-Scenario` (koristi se `-Urgent` switch umesto
   `-Scenario 'Alarm'` za HIGH). Stari API iz dokumentacije/primera vise
   ne postoji u ovoj verziji modula.
4. `-AppLogo` na `New-BurntToastNotification` u ovoj verziji ocekuje
   **string putanju**, ne vec izgradjen `New-BTImage` objekat (modul ga
   sam interno gradi). Slanje objekta ga je tiho pretvaralo u njegov
   .NET tip-name string, sto je znacilo da se RugBuster logo NIKAD nije
   stvarno prikazivao na toast-u (bez greske, samo tih fallback na
   generalnu ikonicu).
5. **Kriticno**: i tajmer za skeniranje i `FormClosing` handler su
   referencirali lokalnu promenljivu (`$timer` / `$form`) iz svog
   sopstvenog event-handler scriptblock-a. Kako `Build-MainForm` vraca
   pre nego sto `Application.Run` uopste pokrene event loop, taj lokalni
   scope vise ne postoji kad handler stvarno okine — `$timer`/`$form`
   su bili `$null` iznutra. Ovo je u potpunosti onesposobilo adaptivni
   5s->10s backoff (KORAK 4) i "minimize to tray" na X dugme (klik na X
   ni zatvarao ni sakrivao prozor). Popravljeno referenciranjem
   `$Script:ScanTimer` odn. event-ovog `$s` (sender) umesto lokalnih
   promenljivih.
6. `Resolve-RemoteHostName` je radila neogranicen, nekesiran
   `[System.Net.Dns]::GetHostEntry()` poziv sinhrono na UI thread-u za
   SVAKU konekciju u SVAKOM ciklusu skeniranja. Na masini sa vise
   desetina aktivnih konekcija (mnoge bez PTR zapisa, sto ume da traje
   vise sekundi po pokusaju) ovo je izmereno da naduva jedan scan ciklus
   na 140+ sekundi i potpuno zamrzne GUI (`IsHungAppWindow` = true).
   Popravljeno: keširanje po IP (svaki se resolvuje samo jednom) +
   `GetHostEntryAsync` sa 300ms limitom po novom lookup-u.

Nakon ovih ispravki: BurntToast toast se prikazuje bez upozorenja/gresaka
(potvrdjeno logovima), "Detalji"/tray/X-dugme IPC ciklus (pipe signal ->
`Show-MainWindow` -> prozor postaje vidljiv/sakriven) je proveren
programatski preko named pipe-a i Win32 `IsWindowVisible`, a adaptivni
interval je uzivo potvrdjen kako ispravno prelazi sa 5000ms na 10000ms
kad skeniranje predugo traje (status traka: `interval: 10000ms`).

**Sta jos NIJE vizuelno potvrdjeno** (nedostupan bio je computer-use
pristup terminal-hostovanom prozoru u ovoj sesiji): da toast fizicki
iskace u donjem desnom uglu sa ispravno renderovanim krug-isecenim
RugBuster logom (samo je programski potvrdjeno da poziv ne baca vise
gresku/upozorenje), i da klik na "Detalji" vizuelno selektuje tacan red
u tabu "Istorija upozorenja" (UI Automation stablo za ovaj WinForms
grid je previse plitko da bi se to programski procitalo). Namerno
pokretanje nepotpisanog .exe koji odmah otvara konekciju NIJE posebno
testirano — HIGH putanja je ipak organski potvrdjena uzivo kad je
skener sam uhvatio stvaran nepotpisan proces (`ula.exe`,
`C:\Program Files\Chaos\UnifiedLogin\ula.exe`) na ovoj masini.
