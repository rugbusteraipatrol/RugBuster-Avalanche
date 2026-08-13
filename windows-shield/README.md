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

## Poznata ogranicenja / sta NIJE testirano uzivo

Ovaj kod je pisan i pregledan u Linux sandbox okruzenju bez Windows
runtime-a — **nema pristupa PowerShell/WinForms/BurntToast/Windows 11
toast sistemu u ovom okruzenju**, pa skripta nije mogla da bude pokrenuta
ili klik-testirana end-to-end ovde. Pre nego sto se proglasi "gotovo":

1. Pokreni na pravoj Windows 11 masini: `powershell.exe -File
   .\RugBuster-Shield-GUI.ps1` (obican korisnicki nalog je dovoljan;
   `Get-NetTCPConnection` i `Get-AuthenticodeSignature` ne traze admin).
2. Proveri da BurntToast toast zaista iskace dole desno sa RugBuster
   logom, i da dugme "Detalji" otvara/fokusira glavni prozor na
   odgovarajucem redu.
3. Proveri CPU opterecenje pri 5s intervalu (Task Manager) — ako je
   primetno, potvrdi da adaptivni "backoff" na 10s (u `Get-AdaptiveInterval`)
   radi kako treba.
4. Namerno pokreni nepotpisan .exe koji otvara konekciju da potvrdis HIGH
   putanju, i dodaj/ukloni proces iz `rugbuster_whitelist.txt` da potvrdis
   da whitelist zaista gusi MEDIUM/HIGH.
