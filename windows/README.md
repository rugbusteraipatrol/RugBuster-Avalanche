# RugBuster Shield GUI (Windows desktop companion)

A PowerShell + WinForms desktop app that watches running processes and
their outbound TCP connections, scores them with a 3-level severity model,
and raises Windows 11 toast notifications through the [BurntToast][burnt]
module. This is a host-based network/process monitor, separate from the
Avalanche on-chain scanner in the rest of this repo — it shares the
RugBuster brand but not the codebase.

[burnt]: https://github.com/Windos/BurntToast

## Important: not runnable/tested in this repo's CI environment

This tool and its `.ps1` were written from a Linux cloud container with no
Windows, no WinForms runtime, no `Get-NetTCPConnection`/`Get-AuthenticodeSignature`,
and no BurntToast. **It has not been executed or visually verified.** Before
relying on it, run it on a real Windows 10/11 machine and walk through the
"Manual test checklist" below.

## Branding source

Extracted 2026-08-13 from the live `rugbuster.io` site
(`rugbusteraipatrol/rugbuster-website`, `index.html`):

| Token | Value |
|---|---|
| `--neon-cyan` | `#00F5FF` |
| `--neon-pink` | `#FF00C8` |
| `--neon-green` | `#00FF88` |
| `--neon-orange` | `#FF6B00` |
| danger red | `#FF3D3D` |
| `--dark-bg` | `#050811` |
| `--dark-panel` | `#0A0F1E` |
| `--dark-card` | `#0D1527` |
| `--text-primary` | `#E8F4FF` |
| `--text-muted` | `#5A7A9A` |
| Fonts | Orbitron (headings), Rajdhani (body), Share Tech Mono (labels) |

`rugbuster.io`'s "logo" is CSS text (`RUG` + `BUSTER`, neon glow) — there is
no image/SVG/ICO asset anywhere in the org to copy. `RugBuster-Shield-GUI.ps1`
therefore **draws** a shield/"RB" icon at runtime in the brand colors
(`New-RugBusterIcon`) and caches it as `rugbuster_logo.ico` / `.png` next to
the script, used for the header panel, the system tray icon, and the toast
`AppLogo`.

Orbitron/Rajdhani/Share Tech Mono are Google Fonts; WinForms can't fetch web
fonts. `Get-RugBusterFont` uses the real family only if it's installed
locally (install them from fonts.google.com for a pixel-accurate look) and
otherwise falls back to Consolas/Segoe UI, which keeps the terminal look
close enough without extra setup.

## Setup

```powershell
Install-Module BurntToast -Scope CurrentUser
```

Then just run the script:

```powershell
.\RugBuster-Shield-GUI.ps1
```

No admin rights are required. The script:

- creates `rugbuster_whitelist.txt`, `rugbuster_state.json`, and
  `rugbuster_alert_history.json` next to itself on first run if they don't
  already exist (a starter whitelist ships in this folder — edit it by
  hand any time);
- registers a `rugbuster:` URI protocol under `HKCU\Software\Classes` so a
  toast's "Detalji" button can bring the already-running window to front
  and jump to that alert row (`-FocusAlertId`), even though Windows starts
  button activations as a new process — the new process detects the
  existing single-instance mutex, hands off the alert id through a small
  signal file, and exits;
- uses a named mutex (`Global\RugBusterShieldGUI_Mutex`) so only one
  instance runs at a time.

## Severity model (KORAK 3)

Documented in code at `Get-RugBusterSeverity` in `RugBuster-Shield-GUI.ps1`:

- **LOW** (green, log-only, no toast) — whitelisted process, or a
  (process, destination) pair already seen before.
- **MEDIUM** / "WORM" (orange, quiet toast) — not whitelisted, and either
  the process name is new or a known process is talking to a destination
  it hasn't used before.
- **HIGH** / "DANGER" (red, loud toast, pinned until resolved) — not
  whitelisted, and any of:
  - `Get-AuthenticodeSignature` returns `NotSigned` or `HashMismatch`;
  - the executable's `LastWriteTime`/`CreationTime` is under 24h old
    (`$Config.NewFileWindowHours`) and it's opening an external connection
    right now;
  - (optional, **disabled by default** — `$Config.EnableBurstHeuristic`) a
    burst of bytes to an unseen destination over a short window, using the
    `Process(...)\IO Data Bytes/sec` performance counter. This is a
    best-effort signal only — Windows doesn't expose per-connection byte
    counts, so it can't be tied to one specific remote endpoint, which is
    why it ships off. Turn it on only after checking that counter behaves
    acceptably on your target machines.

Whitelisted processes short-circuit to LOW before any HIGH check runs, so
a whitelisted entry can never fire MEDIUM/HIGH (KORAK 5).

Scan cadence starts at 5s (KORAK 4) and self-adapts: if a scan tick takes
more than 50% of the current interval, the app drops to a 10s cadence and
logs a warning (`$Config.CpuBudgetRatio`, `$Config.IntervalMsSlow`).

## Manual test checklist (run on real Windows 10/11 — not done by Claude)

- [ ] `Install-Module BurntToast` succeeds and `Import-Module BurntToast`
      loads without error.
- [ ] `.\RugBuster-Shield-GUI.ps1` opens a window with the shield icon in
      the top-left header and in the system tray.
- [ ] Closing the window (X) minimizes to tray instead of exiting; tray
      icon double-click / "Otvori" restores it; "Izlaz" actually quits.
- [ ] Open a browser (whitelisted) — new connections show up in Monitor as
      LOW/green and never raise a toast.
- [ ] Run an unfamiliar/unsigned test binary that opens a connection (e.g.
      a throwaway self-signed or unsigned exe in a sandbox VM) — confirm
      it raises a HIGH/red toast with sound, that the toast stays pinned
      until dismissed, and that clicking "Detalji" focuses the right row
      in "Istorija upozorenja" (including when the main window was
      previously closed to tray).
- [ ] Trigger a MEDIUM case (a whitelisted-adjacent but not-whitelisted
      app hitting a new domain) — confirm the toast is silent/quiet.
- [ ] Click "Lazna uzbuna" on a history row — confirm the process name is
      appended to `rugbuster_whitelist.txt` and stops alerting.
- [ ] Click "Istrazeno - OK" — confirm the row's status updates and
      persists across an app restart (`rugbuster_alert_history.json`).
- [ ] Watch CPU usage over a few minutes; confirm the interval bumps to
      10s automatically on a loaded machine, or stays at 5s and is
      acceptable on a quiet one.
- [ ] Confirm Orbitron/Rajdhani/Share Tech Mono render if installed, and
      that the UI still looks reasonable with the Consolas/Segoe UI
      fallback if they aren't.
