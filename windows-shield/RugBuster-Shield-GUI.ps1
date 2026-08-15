<#
.SYNOPSIS
    RugBuster Shield - live process/network monitor with WinForms GUI,
    system tray icon and Windows 11 toast notifications.

.DESCRIPTION
    Watches active outbound TCP connections, correlates them to the owning
    process, and flags anything suspicious using a 3-tier severity model
    (LOW / MEDIUM ("WORM") / HIGH ("DANGER")). See Get-ConnectionSeverity
    for the exact rules and thresholds.

    Branding (colors / fonts / visual language) was extracted verbatim from
    rugbusteraipatrol/rugbuster-website (index.html :root custom
    properties) - see $Script:Brand below for the source values. The logo
    itself (windows-shield/assets/rugbuster-shield-logo.png / .ico) is the
    org's real illustrated shield logo, copied from
    rugbusteraipatrol/rugbuster-multichain's docs/favicon.png (same file
    also ships in RugBuster-CIA-Lab) - used as the header/tray/toast icon.

.NOTES
    Windows 11 + PowerShell 5.1/7 with .NET WinForms only. This script was
    authored and reviewed in a Linux sandbox that has no Windows runtime,
    so it could NOT be launched or click-tested end to end here - review
    the code and run it on a real Windows 11 box before relying on it.
    Requires the BurntToast module for toast notifications (auto-installed
    on first run if missing; falls back to a tray balloon tip if BurntToast
    cannot be installed, e.g. no internet / no PSGallery access).
#>

[CmdletBinding()]
param(
    # Populated automatically when Windows activates our custom
    # "rugbuster-shield:" protocol (e.g. from a toast button click).
    # Format: rugbuster-shield:<action>?id=<alert-guid>
    [string]$Signal
)

# ============================================================================
#region CONFIG - brand tokens, paths, severity thresholds
# ============================================================================

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

# DWM P/Invoke for a branded (dark, cyan-accented) native title bar - by
# default WinForms leaves the OS-drawn caption/border white/light even when
# the window content is fully dark-themed, which reads as a generic "classic
# Windows program" frame bolted onto a themed app. DWMWA_CAPTION_COLOR /
# DWMWA_TEXT_COLOR / DWMWA_BORDER_COLOR (Windows 11 22000+) let the native
# non-client area itself be recolored to match the rest of the ecosystem
# (rugbuster.io, this same GUI's own header) instead of custom-drawing a
# borderless window (drag/hit-testing/snap-layout handling) for the same result.
Add-Type -Name DwmNative -Namespace RugBusterShield -MemberDefinition @'
[DllImport("dwmapi.dll")]
public static extern int DwmSetWindowAttribute(IntPtr hwnd, int attr, ref int value, int size);
'@

# This is a GUI app - a plain `powershell.exe -File ...` launch (anything
# other than the toast-relay path, which already passes -WindowStyle Hidden)
# leaves the interpreter's own console host window sitting there in the OS
# default white/light theme, visually clashing right next to the themed
# WinForms window. Hide it outright rather than trying to theme it - it
# serves no purpose once the GUI takes over.
Add-Type -Name ConsoleNative -Namespace RugBusterShield -MemberDefinition @'
[DllImport("kernel32.dll")]
public static extern IntPtr GetConsoleWindow();
[DllImport("user32.dll")]
public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);
'@
$Script:ConsoleHwnd = [RugBusterShield.ConsoleNative]::GetConsoleWindow()
if ($Script:ConsoleHwnd -ne [IntPtr]::Zero) {
    [void][RugBusterShield.ConsoleNative]::ShowWindow($Script:ConsoleHwnd, 0) # SW_HIDE
}

# Native scrollbars (DataGridView's own vertical/horizontal ScrollBar child
# controls) stay OS-default light grey even inside an otherwise fully dark
# window - SetWindowTheme(..., "DarkMode_Explorer") is the standard trick to
# get Windows' own dark-mode scrollbar visuals, but it only affects the exact
# HWND you call it on, not children, so walk the control's child windows too.
Add-Type -Namespace RugBusterShield -Name ScrollBarTheme -MemberDefinition @'
[DllImport("uxtheme.dll", CharSet = CharSet.Unicode)]
public static extern int SetWindowTheme(IntPtr hWnd, string pszSubAppName, string pszSubIdList);
[DllImport("user32.dll")]
public static extern bool EnumChildWindows(IntPtr hwndParent, EnumChildWindowsProc lpEnumFunc, IntPtr lParam);
public delegate bool EnumChildWindowsProc(IntPtr hwnd, IntPtr lParam);
// Undocumented but widely-used (ordinal export, no header/declaration exists) -
// SetWindowTheme alone is not enough on its own to make comctl32's stock
// ScrollBar controls (which is what DataGridView's internal scrollbars are)
// actually paint dark; the process also has to opt in to dark mode for
// standard controls via this ordinal, or SetWindowTheme is a no-op for them.
[DllImport("uxtheme.dll", EntryPoint = "#135")]
public static extern int SetPreferredAppMode(int preferredAppMode);
public static void ApplyDark(IntPtr hwnd) {
    SetWindowTheme(hwnd, "DarkMode_Explorer", null);
    EnumChildWindows(hwnd, delegate(IntPtr child, IntPtr lp) {
        SetWindowTheme(child, "DarkMode_Explorer", null);
        return true;
    }, IntPtr.Zero);
}
'@
[void][RugBusterShield.ScrollBarTheme]::SetPreferredAppMode(2) # 2 = ForceDark

function Set-BrandTitleBar {
    param([Parameter(Mandatory)][System.Windows.Forms.Form]$Form)
    $DWMWA_USE_IMMERSIVE_DARK_MODE = 20
    $DWMWA_BORDER_COLOR = 34
    $DWMWA_CAPTION_COLOR = 35
    $DWMWA_TEXT_COLOR = 36
    # DWM colors are 0x00BBGGRR, the reverse byte order of System.Drawing.Color.
    # R/G/B are [byte] - PowerShell's -shl keeps byte-typed operands in 8-bit
    # arithmetic (245 -shl 8 silently overflows to 0 instead of widening), so
    # cast to [int] first or every channel above R collapses to 0.
    $toColorRef = { param($c) [int]$c.R -bor ([int]$c.G -shl 8) -bor ([int]$c.B -shl 16) }
    $hwnd = $Form.Handle
    $darkMode = 1
    [void][RugBusterShield.DwmNative]::DwmSetWindowAttribute($hwnd, $DWMWA_USE_IMMERSIVE_DARK_MODE, [ref]$darkMode, 4)
    $captionRef = & $toColorRef $Script:Brand.Panel
    [void][RugBusterShield.DwmNative]::DwmSetWindowAttribute($hwnd, $DWMWA_CAPTION_COLOR, [ref]$captionRef, 4)
    $textRef = & $toColorRef $Script:Brand.Cyan
    [void][RugBusterShield.DwmNative]::DwmSetWindowAttribute($hwnd, $DWMWA_TEXT_COLOR, [ref]$textRef, 4)
    $borderRef = & $toColorRef $Script:Brand.Cyan
    [void][RugBusterShield.DwmNative]::DwmSetWindowAttribute($hwnd, $DWMWA_BORDER_COLOR, [ref]$borderRef, 4)
    # Older Windows 10 builds (pre-22H2/11) only support the dark-mode flag,
    # not custom caption/text/border colors - DwmSetWindowAttribute simply
    # returns a non-zero HRESULT for the unsupported attributes there, so
    # this degrades gracefully to a plain dark title bar instead of erroring.
}

$Script:RootDir    = Split-Path -Parent $MyInvocation.MyCommand.Path
$Script:AssetsDir  = Join-Path $Script:RootDir 'assets'
$Script:LogoPng    = Join-Path $Script:AssetsDir 'rugbuster-shield-logo.png'
$Script:LogoIco    = Join-Path $Script:AssetsDir 'rugbuster-shield-logo.ico'
$Script:WhitelistFile      = Join-Path $Script:RootDir 'rugbuster_whitelist.txt'
$Script:AlertHistoryFile   = Join-Path $Script:RootDir 'rugbuster_alert_history.json'
$Script:SeenStateFile      = Join-Path $Script:RootDir 'rugbuster_seen_state.json'

# --- Brand tokens - extracted from rugbusteraipatrol/rugbuster-website ---
# (index.html, :root custom properties + severity color usage in the
# scan feed / portfolio risk badges: .r-danger / .r-warn / .r-good).
$Script:Brand = @{
    BgDark      = [System.Drawing.ColorTranslator]::FromHtml('#050811')  # --dark-bg
    Panel       = [System.Drawing.ColorTranslator]::FromHtml('#0a0f1e')  # --dark-panel
    Card        = [System.Drawing.ColorTranslator]::FromHtml('#0d1527')  # --dark-card
    Cyan        = [System.Drawing.ColorTranslator]::FromHtml('#00f5ff')  # --neon-cyan (primary accent)
    Pink        = [System.Drawing.ColorTranslator]::FromHtml('#ff00c8')  # --neon-pink (secondary accent)
    Green       = [System.Drawing.ColorTranslator]::FromHtml('#00ff88')  # --neon-green  -> LOW
    Orange      = [System.Drawing.ColorTranslator]::FromHtml('#ff6b00')  # --neon-orange -> MEDIUM
    Danger      = [System.Drawing.ColorTranslator]::FromHtml('#ff3d3d')  # site's dedicated danger red -> HIGH
    TextPrimary = [System.Drawing.ColorTranslator]::FromHtml('#e8f4ff')  # --text-primary
    TextMuted   = [System.Drawing.ColorTranslator]::FromHtml('#5a7a9a')  # --text-muted
    BorderLine  = [System.Drawing.Color]::FromArgb(40, 0, 245, 255)      # rgba(0,245,255,.16) borders on the site
    # DataGridView.GridColor throws "cannot be set to a transparent color" for any
    # alpha != 255, so pre-blend the same rgba(0,245,255,.16) border color over
    # --dark-bg (#050811) into an opaque equivalent for use as GridColor.
    GridLine    = [System.Drawing.Color]::FromArgb(4, 45, 54)
}

# Fonts on the live site are Google Fonts (Orbitron / Rajdhani / Share Tech
# Mono) loaded over the web - they are almost never pre-installed on a
# Windows box. System.Drawing.Font silently falls back to the nearest
# installed font if the family name isn't found, so we just reference the
# brand names directly; install the fonts locally for a pixel-perfect
# match, otherwise this degrades gracefully to Consolas/Segoe UI.
$Script:Brand.FontDisplay = 'Orbitron'        # headings / logo wordmark
$Script:Brand.FontBody    = 'Rajdhani'        # body text / labels
$Script:Brand.FontMono    = 'Share Tech Mono' # grid / log / terminal-style text

function New-BrandFont {
    param(
        [ValidateSet('Display', 'Body', 'Mono')] [string]$Kind = 'Body',
        [single]$Size = 9,
        [System.Drawing.FontStyle]$Style = [System.Drawing.FontStyle]::Regular
    )
    $family = switch ($Kind) {
        'Display' { $Script:Brand.FontDisplay }
        'Mono'    { $Script:Brand.FontMono }
        default   { $Script:Brand.FontBody }
    }
    try {
        return New-Object System.Drawing.Font($family, $Size, $Style)
    } catch {
        # Family not installed -> let WinForms pick a safe fallback.
        $fallback = if ($Kind -eq 'Mono') { 'Consolas' } else { 'Segoe UI' }
        return New-Object System.Drawing.Font($fallback, $Size, $Style)
    }
}

# --- Scan cadence (KORAK 4) ------------------------------------------------
# Starts at 5s as requested. If a scan cycle keeps taking too long relative
# to the interval (i.e. we'd be pegging the CPU running back-to-back scans)
# we back off to 10s automatically - see Get-AdaptiveInterval.
$Script:Config = @{
    IntervalFastMs   = 5000
    IntervalSlowMs   = 10000
    CurrentInterval  = 5000
    # If the last scan took more than this fraction of the interval, back off.
    CpuBackoffRatio  = 0.5
    ScanDurationsMs  = New-Object System.Collections.Generic.Queue[double]
    NewFileWindowHrs = 24   # KORAK 3(b): "fajl nastao u poslednja 24h"
    EnableBandwidthHeuristic = $false # KORAK 3(c) - see Test-BandwidthSpike
}

#endregion

# ============================================================================
#region STATE - whitelist, seen-connections memory, alert history (persisted)
# ============================================================================

# $Script:KnownProcesses : HashSet<string> of process names ever seen
#                          (lower-case) -> used to flag brand-new processes.
# $Script:SeenDestinations : Hashtable "<procname>" -> HashSet<string> of
#                          "<remoteHost or IP>" ever seen for that process
#                          -> used to flag a known process on a new domain.
$Script:KnownProcesses   = New-Object 'System.Collections.Generic.HashSet[string]'
$Script:SeenDestinations = @{}
$Script:AlertHistory     = New-Object System.Collections.ArrayList
$Script:Whitelist        = New-Object 'System.Collections.Generic.HashSet[string]'
# Processes already toasted at HIGH severity this run - see Invoke-RugBusterScan.
$Script:ToastedProcesses = New-Object 'System.Collections.Generic.HashSet[string]'

function Import-Whitelist {
    $Script:Whitelist.Clear()
    if (-not (Test-Path $Script:WhitelistFile)) { return }
    foreach ($line in Get-Content -Path $Script:WhitelistFile) {
        $t = $line.Trim()
        if ([string]::IsNullOrWhiteSpace($t) -or $t.StartsWith('#')) { continue }
        [void]$Script:Whitelist.Add($t.ToLowerInvariant())
    }
}

function Add-ToWhitelist {
    param([Parameter(Mandatory)][string]$ProcessName)
    $name = $ProcessName.ToLowerInvariant()
    if ($Script:Whitelist.Contains($name)) { return }
    [void]$Script:Whitelist.Add($name)
    Add-Content -Path $Script:WhitelistFile -Value $ProcessName
}

function Test-IsWhitelisted {
    param([Parameter(Mandatory)][string]$ProcessName)
    return $Script:Whitelist.Contains($ProcessName.ToLowerInvariant())
}

function Save-SeenState {
    $payload = @{
        KnownProcesses   = @($Script:KnownProcesses)
        SeenDestinations = @{}
    }
    foreach ($k in $Script:SeenDestinations.Keys) {
        $payload.SeenDestinations[$k] = @($Script:SeenDestinations[$k])
    }
    $payload | ConvertTo-Json -Depth 5 | Set-Content -Path $Script:SeenStateFile -Encoding UTF8
}

function Import-SeenState {
    if (-not (Test-Path $Script:SeenStateFile)) { return }
    try {
        $data = Get-Content -Path $Script:SeenStateFile -Raw | ConvertFrom-Json
        foreach ($p in $data.KnownProcesses) { [void]$Script:KnownProcesses.Add($p) }
        foreach ($prop in $data.SeenDestinations.PSObject.Properties) {
            $set = New-Object 'System.Collections.Generic.HashSet[string]'
            foreach ($d in $prop.Value) { [void]$set.Add($d) }
            $Script:SeenDestinations[$prop.Name] = $set
        }
    } catch {
        Write-Warning "Could not load rugbuster_seen_state.json: $($_.Exception.Message)"
    }
}

function Save-AlertHistory {
    ($Script:AlertHistory | ConvertTo-Json -Depth 5) | Set-Content -Path $Script:AlertHistoryFile -Encoding UTF8
}

function Import-AlertHistory {
    $Script:AlertHistory.Clear()
    if (-not (Test-Path $Script:AlertHistoryFile)) { return }
    try {
        $data = Get-Content -Path $Script:AlertHistoryFile -Raw | ConvertFrom-Json
        foreach ($item in @($data)) { [void]$Script:AlertHistory.Add($item) }
    } catch {
        Write-Warning "Could not load rugbuster_alert_history.json: $($_.Exception.Message)"
    }
}

#endregion

# ============================================================================
#region UTILITY - signature check, reverse DNS, adaptive interval
# ============================================================================

$Script:SignatureCache = @{}

function Get-SignatureStatus {
    # Same class of bug as the DNS one below: Get-AuthenticodeSignature does
    # real certificate-chain/revocation validation and was measured taking
    # up to ~1 second for a single file (Dropbox.exe) - and this runs
    # unconditionally, uncached, per CONNECTION per SCAN, on the UI thread.
    # A process with a few open sockets pays that ~1s cost that many times
    # every single scan cycle forever, which is what was actually causing
    # the residual multi-second "Not Responding" freezes reported after the
    # DNS fix. A running process's on-disk file signature can't meaningfully
    # change while it's running, so cache per path for the life of the app.
    param([Parameter(Mandatory)][string]$FilePath)
    if ($Script:SignatureCache.ContainsKey($FilePath)) { return $Script:SignatureCache[$FilePath] }
    $status = 'NotSigned'
    try {
        $sig = Get-AuthenticodeSignature -FilePath $FilePath -ErrorAction Stop
        $status = $sig.Status.ToString()
    } catch { }
    $Script:SignatureCache[$FilePath] = $status
    return $status
}

$Script:DnsCache = @{}
$Script:DnsLookupTimeoutMs = 300

function Resolve-RemoteHostName {
    # Reverse DNS lookups run synchronously on the WinForms UI thread once per
    # scan per connection, so an unbounded/uncached GetHostEntry call here can
    # freeze the whole GUI: on a box with dozens of established connections,
    # repeatedly re-resolving the same handful of IPs (many with no PTR record,
    # which can take multiple seconds each to fail) was measured to blow a
    # single scan out to 140+ seconds. Cache every result (success or fallback)
    # per IP so it's only ever looked up once, and bound each new lookup so a
    # slow/hanging resolver can't stall the UI for more than $DnsLookupTimeoutMs.
    param([Parameter(Mandatory)][string]$IPAddress)
    if ($Script:DnsCache.ContainsKey($IPAddress)) { return $Script:DnsCache[$IPAddress] }
    $result = $IPAddress
    try {
        $task = [System.Net.Dns]::GetHostEntryAsync($IPAddress)
        if ($task.Wait($Script:DnsLookupTimeoutMs) -and $task.Result -and $task.Result.HostName) {
            $result = $task.Result.HostName
        }
    } catch {
        # No PTR record / resolution failed / timed out - fall back to the raw IP.
    }
    $Script:DnsCache[$IPAddress] = $result
    return $result
}

function Get-AdaptiveInterval {
    # KORAK 4: start at 5s; back off to 10s if scans are eating too much
    # of the interval (rolling average over the last 5 cycles).
    $q = $Script:Config.ScanDurationsMs
    if ($q.Count -eq 0) { return $Script:Config.IntervalFastMs }
    $avg = ($q | Measure-Object -Average).Average
    if ($avg -gt ($Script:Config.IntervalFastMs * $Script:Config.CpuBackoffRatio)) {
        return $Script:Config.IntervalSlowMs
    }
    return $Script:Config.IntervalFastMs
}

function Test-BandwidthSpike {
    # KORAK 3(c), best-effort/optional per the spec: flag a large burst of
    # data to the same unknown destination in a short window. Disabled by
    # default ($Config.EnableBandwidthHeuristic = $false) because sampling
    # per-process byte counters (Get-Counter '\Process(*)\IO Data Bytes/sec')
    # every 5s adds real overhead and per-connection (not just per-process)
    # attribution isn't reliably available without a packet-capture driver.
    # Flip the flag on to enable a coarse per-process approximation.
    param([Parameter(Mandatory)][string]$ProcessName)
    if (-not $Script:Config.EnableBandwidthHeuristic) { return $false }
    try {
        $counterPath = "\Process($ProcessName)\IO Data Bytes/sec"
        $sample = (Get-Counter -Counter $counterPath -ErrorAction Stop).CounterSamples |
            Select-Object -First 1
        # Arbitrary "spike" threshold: >5 MB/s sustained from one process.
        return ($sample.CookedValue -gt 5MB)
    } catch {
        return $false
    }
}

#endregion

# ============================================================================
#region SEVERITY ENGINE (KORAK 3)
# ============================================================================
<#
    Severity pragovi (documented per the task's KORAK 3):

    LOW    (zeleno, $Brand.Green)   - poznat proces NA poznatoj destinaciji
                                       (ukljucujuci sve na whitelisti).
                                       Samo se loguje - BEZ notifikacije.

    MEDIUM ("WORM", $Brand.Orange)  - bilo koji od:
                                         a) proces koji NIKAD ranije nije
                                            vidjen (novi proces), ili
                                         b) poznat proces koji se prvi put
                                            povezuje na ovu destinaciju.
                                       -> tiha notifikacija (BurntToast -Silent).

    HIGH   ("DANGER", $Brand.Danger) - bilo koji od:
                                         a) Get-AuthenticodeSignature vraca
                                            'NotSigned' ili 'HashMismatch'
                                         b) fajl procesa je izmenjen u
                                            poslednjih $NewFileWindowHrs (24h)
                                            I ovo je nova eksterna konekcija
                                         c) (opciono/best-effort) bandwidth
                                            spike ka nepoznatoj destinaciji -
                                            vidi Test-BandwidthSpike
                                       -> glasna, "pinned" notifikacija
                                          (Scenario Alarm) dok se rucno ne
                                          potvrdi/odbaci u Istoriji upozorenja.

    Whitelisted procesi (rugbuster_whitelist.txt) su UVEK LOW, bez obzira
    na gornje uslove (KORAK 5).
#>
function Get-ConnectionSeverity {
    param(
        [Parameter(Mandatory)] [string]$ProcessName,
        [Parameter(Mandatory)] [string]$RemoteHost,
        [string]$ProcessPath,
        [bool]$IsNewProcess,
        [bool]$IsNewDestination
    )

    $result = [ordered]@{
        Severity = 'LOW'
        Reasons  = New-Object System.Collections.Generic.List[string]
        Signed   = $true
    }

    if (Test-IsWhitelisted -ProcessName $ProcessName) {
        return $result
    }

    if ($IsNewProcess) {
        $result.Severity = 'MEDIUM'
        $result.Reasons.Add('New process - first seen on this system')
    }
    if ($IsNewDestination) {
        $result.Severity = 'MEDIUM'
        $result.Reasons.Add("Known process, new destination: $RemoteHost")
    }

    if ($ProcessPath -and (Test-Path $ProcessPath)) {
        # --- HIGH (a): digital signature ---
        $sigStatus = Get-SignatureStatus -FilePath $ProcessPath
        if ($sigStatus -in @('NotSigned', 'HashMismatch')) {
            $result.Signed = $false
            $result.Severity = 'HIGH'
            $result.Reasons.Add("Unsigned/tampered file (Authenticode: $sigStatus)")
        }

        # --- HIGH (b): file is new (<24h) + connects out immediately ---
        try {
            $lastWrite = (Get-Item -Path $ProcessPath -ErrorAction Stop).LastWriteTime
            $ageHours = (New-TimeSpan -Start $lastWrite -End (Get-Date)).TotalHours
            if ($ageHours -ge 0 -and $ageHours -lt $Script:Config.NewFileWindowHrs -and ($IsNewProcess -or $IsNewDestination)) {
                $result.Severity = 'HIGH'
                $result.Reasons.Add(('File modified {0:N1}h ago and immediately makes an external connection' -f $ageHours))
            }
        } catch { }
    }

    # --- HIGH (c): best-effort bandwidth heuristic (optional) ---
    if (Test-BandwidthSpike -ProcessName $ProcessName) {
        $result.Severity = 'HIGH'
        $result.Reasons.Add('Unusually large amount of data to an unknown destination')
    }

    return $result
}

#endregion

# ============================================================================
#region IPC - "rugbuster-shield:" protocol + named pipe (toast button -> GUI)
# ============================================================================

$Script:PipeName  = 'RugBusterShieldPipe'
$Script:PipeQueue = New-Object 'System.Collections.Concurrent.ConcurrentQueue[string]'
$Script:PendingSignal = $null

function Register-RugBusterProtocol {
    # HKCU (no admin needed) registration of a custom URI scheme so a toast
    # button ("Detalji") can launch/wake this script with an alert id.
    # rugbuster-shield:show?id=<guid>
    try {
        $root = 'HKCU:\Software\Classes\rugbuster-shield'
        if (-not (Test-Path $root)) { New-Item -Path $root -Force | Out-Null }
        Set-Item -Path $root -Value 'URL:RugBuster Shield Protocol'
        New-ItemProperty -Path $root -Name 'URL Protocol' -Value '' -PropertyType String -Force | Out-Null

        $cmdKey = "$root\shell\open\command"
        if (-not (Test-Path $cmdKey)) { New-Item -Path $cmdKey -Force | Out-Null }
        $hostExe = (Get-Process -Id $PID).Path
        $cmd = '"{0}" -NoProfile -WindowStyle Hidden -File "{1}" -Signal "%1"' -f $hostExe, $MyInvocation.MyCommand.Path
        Set-Item -Path $cmdKey -Value $cmd
    } catch {
        Write-Warning "Failed to register the rugbuster-shield: protocol (continuing without click-from-toast): $($_.Exception.Message)"
    }
}

function Start-RugBusterPipeServer {
    # Background runspace that blocks on NamedPipeServerStream.WaitForConnection()
    # and forwards each received line into $Script:PipeQueue, drained by a
    # WinForms timer on the UI thread (cross-thread WinForms calls are unsafe
    # otherwise).
    $rs = [runspacefactory]::CreateRunspace()
    $rs.Open()
    $rs.SessionStateProxy.SetVariable('Queue', $Script:PipeQueue)
    $rs.SessionStateProxy.SetVariable('PipeName', $Script:PipeName)

    $ps = [PowerShell]::Create()
    $ps.Runspace = $rs
    [void]$ps.AddScript({
        while ($true) {
            try {
                $pipe = New-Object System.IO.Pipes.NamedPipeServerStream(
                    $PipeName, [System.IO.Pipes.PipeDirection]::In)
                $pipe.WaitForConnection()
                $reader = New-Object System.IO.StreamReader($pipe)
                $line = $reader.ReadLine()
                if ($line) { $Queue.Enqueue($line) }
                $reader.Dispose()
                $pipe.Dispose()
            } catch {
                Start-Sleep -Milliseconds 500
            }
        }
    })
    $Script:PipeAsyncHandle = $ps.BeginInvoke()
    $Script:PipePs = $ps
    $Script:PipeRunspace = $rs
}

function Send-RugBusterSignal {
    # A toast "Details" click launches a brand-new relay process that has to
    # connect to the already-running instance's pipe server within its own
    # short lifetime - a 300ms Connect() timeout with no retry turned out to
    # be too tight under real load (e.g. the main instance mid-scan), and a
    # failed send here used to fall through to building a second full GUI
    # (see the single-instance mutex guard in MAIN) instead of just reaching
    # the existing window. Retry a few times before giving up.
    param([Parameter(Mandatory)][string]$Payload, [int]$MaxAttempts = 5)
    for ($attempt = 1; $attempt -le $MaxAttempts; $attempt++) {
        try {
            $client = New-Object System.IO.Pipes.NamedPipeClientStream(
                '.', $Script:PipeName, [System.IO.Pipes.PipeDirection]::Out)
            $client.Connect(1000)
            $writer = New-Object System.IO.StreamWriter($client)
            $writer.WriteLine($Payload)
            $writer.Flush()
            $writer.Dispose()
            $client.Dispose()
            return $true
        } catch {
            Start-Sleep -Milliseconds 200
        }
    }
    return $false
}

#endregion

# ============================================================================
#region TOAST - BurntToast notifications (Viber/Telegram-style, bottom-right)
# ============================================================================

function Ensure-BurntToast {
    if (Get-Module -ListAvailable -Name BurntToast) { return $true }
    try {
        Write-Host 'BurntToast is not installed - attempting Install-Module...'
        Install-Module -Name BurntToast -Scope CurrentUser -Force -ErrorAction Stop
        return $true
    } catch {
        Write-Warning "Failed to install the BurntToast module ($($_.Exception.Message)). Falling back to a NotifyIcon balloon tip."
        return $false
    }
}

function Show-RugBusterToast {
    param([Parameter(Mandatory)][pscustomobject]$Alert)

    $haveBurntToast = Ensure-BurntToast
    if (-not $haveBurntToast) {
        Show-FallbackBalloon -Alert $Alert
        return
    }

    Import-Module BurntToast -ErrorAction SilentlyContinue

    # BurntToast 1.1.0's -AppLogo on New-BurntToastNotification is typed
    # [String] (a raw file path) - it builds the ToastGenericAppLogo itself
    # internally (New-BTImage -Source $AppLogo -AppLogoOverride -Crop Circle).
    # Passing an already-built New-BTImage object here (as older BurntToast
    # versions required) gets silently ToString()'d to its .NET type name,
    # which then fails path resolution and the logo silently never renders -
    # so pass the plain path string instead.
    $logo = $Script:LogoPng

    $text1 = New-BTText -Content $Alert.ProcessName
    $text2 = New-BTText -Content ("{0}  ({1})" -f $Alert.RemoteHost, $Alert.Severity)

    $btnDetails = New-BTButton -Content 'Details' `
        -Arguments "rugbuster-shield:show?id=$($Alert.Id)" -ActivationType Protocol

    $buttons = @($btnDetails)
    if ($Alert.Severity -ne 'LOW') {
        $buttons += New-BTButton -Content 'False Alarm' `
            -Arguments "rugbuster-shield:falsealarm?id=$($Alert.Id)" -ActivationType Protocol
        $buttons += New-BTButton -Content 'Investigated - OK' `
            -Arguments "rugbuster-shield:investigated?id=$($Alert.Id)" -ActivationType Protocol
    }
    # New-BurntToastNotification takes buttons directly via -Button (an array of
    # New-BTButton objects) - it has no -Action parameter in BurntToast 1.1.0
    # (New-BTAction/New-BTInput exist for a different, richer scenario).
    $params = @{
        AppLogo   = $logo
        Text      = @($text1, $text2)
        Button    = $buttons
        UniqueIdentifier = "RugBusterShield-$($Alert.Id)"
    }

    switch ($Alert.Severity) {
        'HIGH' {
            # BurntToast 1.1.0 dropped -Scenario (Alarm/Reminder/IncomingCall);
            # -Urgent (scenario "urgent") is the closest equivalent it still
            # exposes - breaks through Focus Assist for high-severity alerts.
            $params.Urgent = $true
        }
        'MEDIUM' {
            # Silent notification.
            $params.Silent = $true
        }
        default {
            # LOW never reaches here (log-only) - see Invoke-RugBusterScan.
        }
    }

    try {
        New-BurntToastNotification @params
    } catch {
        Write-Warning "BurntToast notification failed: $($_.Exception.Message)"
        Show-FallbackBalloon -Alert $Alert
    }
}

function Show-FallbackBalloon {
    param([Parameter(Mandatory)][pscustomobject]$Alert)
    if (-not $Script:TrayIcon) { return }
    $icon = switch ($Alert.Severity) {
        'HIGH'   { [System.Windows.Forms.ToolTipIcon]::Error }
        'MEDIUM' { [System.Windows.Forms.ToolTipIcon]::Warning }
        default  { [System.Windows.Forms.ToolTipIcon]::Info }
    }
    $Script:TrayIcon.BalloonTipIcon = $icon
    $Script:TrayIcon.BalloonTipTitle = "RugBuster Shield - $($Alert.Severity)"
    $Script:TrayIcon.BalloonTipText = "$($Alert.ProcessName) -> $($Alert.RemoteHost)"
    $Script:TrayIcon.ShowBalloonTip(8000)
}

#endregion

# ============================================================================
#region SCAN - enumerate connections, score severity, raise alerts
# ============================================================================

function Invoke-RugBusterScan {
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    $rows = New-Object System.Collections.ArrayList
    $newAlertCount = 0

    try {
        $connections = Get-NetTCPConnection -State Established -ErrorAction Stop |
            Where-Object { $_.RemoteAddress -notin @('127.0.0.1', '::1', '0.0.0.0') }
    } catch {
        Write-Warning "Get-NetTCPConnection failed: $($_.Exception.Message)"
        return
    }

    # Group by owning process so one process with many sockets to the same
    # host doesn't spam separate alerts.
    $byProcess = $connections | Group-Object -Property OwningProcess

    foreach ($group in $byProcess) {
        $procId = $group.Name
        $proc = $null
        try { $proc = Get-Process -Id $procId -ErrorAction Stop } catch { continue }

        $procName = $proc.ProcessName + '.exe'
        $procPath = $null
        try { $procPath = $proc.Path } catch { $procPath = $null }

        $isNewProcess = -not $Script:KnownProcesses.Contains($procName.ToLowerInvariant())
        [void]$Script:KnownProcesses.Add($procName.ToLowerInvariant())

        if (-not $Script:SeenDestinations.ContainsKey($procName)) {
            $Script:SeenDestinations[$procName] = New-Object 'System.Collections.Generic.HashSet[string]'
        }

        foreach ($conn in $group.Group) {
            $remoteHost = Resolve-RemoteHostName -IPAddress $conn.RemoteAddress
            $destKey = "$($conn.RemoteAddress)"
            $isNewDestination = -not $Script:SeenDestinations[$procName].Contains($destKey)
            [void]$Script:SeenDestinations[$procName].Add($destKey)

            $eval = Get-ConnectionSeverity -ProcessName $procName -RemoteHost $remoteHost `
                -ProcessPath $procPath -IsNewProcess $isNewProcess -IsNewDestination $isNewDestination

            $row = [pscustomobject]@{
                Id            = [guid]::NewGuid().ToString()
                # A plain formatted string, not the raw Get-Date object: PowerShell
                # 5.1's ConvertTo-Json serializes [datetime]'s ETS members (the
                # DisplayHint/value/DateTime note properties Get-Date's output
                # actually carries) as a nested object instead of a date string,
                # so alerts reloaded from rugbuster_alert_history.json on the next
                # launch showed a literal "@{value=...; DisplayHint=...}" instead
                # of a date in the history grid. A string round-trips as-is.
                Timestamp     = (Get-Date).ToString('yyyy-MM-dd HH:mm:ss')
                ProcessName   = $procName
                Pid           = $procId
                ProcessPath   = $procPath
                RemoteAddress = $conn.RemoteAddress
                RemoteHost    = $remoteHost
                RemotePort    = $conn.RemotePort
                Severity      = $eval.Severity
                Reasons       = ($eval.Reasons -join '; ')
                Signed        = $eval.Signed
                Status        = 'New'
            }
            [void]$rows.Add($row)

            if ($eval.Severity -eq 'LOW') {
                # LOW is log-only, no notification.
                continue
            }

            [void]$Script:AlertHistory.Add($row)
            $newAlertCount++

            # Only HIGH ("DANGER") actually pops a toast, and only once ever
            # per process: MEDIUM fires on every single new process/new
            # destination combo, which on a busy machine (or right after a
            # fresh install, when everything looks "new") was a toast storm -
            # several stacked notifications every scan, each pushed off
            # screen by the next before it could be read. HIGH conditions
            # like an unsigned file also don't depend on "new" status, so
            # without dedup a still-connected flagged process re-toasts every
            # single scan cycle forever. MEDIUM/HIGH both still land in the
            # Live Monitor / Alert History grids either way - only the toast
            # itself is now HIGH-and-once-per-process.
            if ($eval.Severity -eq 'HIGH' -and -not $Script:ToastedProcesses.Contains($procName)) {
                [void]$Script:ToastedProcesses.Add($procName)
                Show-RugBusterToast -Alert $row
            }
        }
    }

    Save-SeenState
    if ($rows.Count -gt 0 -or $Script:AlertHistory.Count -gt 0) { Save-AlertHistory }

    $sw.Stop()
    $Script:Config.ScanDurationsMs.Enqueue($sw.Elapsed.TotalMilliseconds)
    while ($Script:Config.ScanDurationsMs.Count -gt 5) { [void]$Script:Config.ScanDurationsMs.Dequeue() }
    $Script:Config.CurrentInterval = Get-AdaptiveInterval

    Update-LiveGrid -Rows $rows
    # Refresh-HistoryGrid was previously only called at startup and from the
    # history tab's own action buttons, so new MEDIUM/HIGH alerts landed in
    # $Script:AlertHistory and the toast fired, but the "Istorija upozorenja"
    # tab itself stayed on its startup snapshot until the user clicked
    # something in it - it never showed newly arriving alerts on its own.
    if ($newAlertCount -gt 0) { Refresh-HistoryGrid }
}

#endregion

# ============================================================================
#region GUI
# ============================================================================

function Get-SeverityColor {
    param([string]$Severity)
    switch ($Severity) {
        'HIGH'   { return $Script:Brand.Danger }
        'MEDIUM' { return $Script:Brand.Orange }
        default  { return $Script:Brand.Green }
    }
}

function New-BrandButton {
    param([string]$Text, [System.Drawing.Color]$Accent)
    $btn = New-Object System.Windows.Forms.Button
    $btn.Text = $Text
    $btn.FlatStyle = [System.Windows.Forms.FlatStyle]::Flat
    $btn.FlatAppearance.BorderColor = $Accent
    $btn.FlatAppearance.BorderSize = 1
    $btn.BackColor = $Script:Brand.Card
    $btn.ForeColor = $Accent
    $btn.Font = New-BrandFont -Kind Body -Size 9.5 -Style Bold
    $btn.Height = 30
    $btn.Cursor = [System.Windows.Forms.Cursors]::Hand
    return $btn
}

function Build-MainForm {
    $form = New-Object System.Windows.Forms.Form
    $form.Text = 'RugBuster Shield'
    $form.Size = New-Object System.Drawing.Size(1120, 720)
    $form.MinimumSize = New-Object System.Drawing.Size(900, 560)
    $form.StartPosition = 'CenterScreen'
    $form.BackColor = $Script:Brand.BgDark
    $form.ForeColor = $Script:Brand.TextPrimary
    $form.Font = New-BrandFont -Kind Body -Size 9.5
    if (Test-Path $Script:LogoIco) {
        $form.Icon = New-Object System.Drawing.Icon($Script:LogoIco)
    }
    Set-BrandTitleBar -Form $form

    # --- Header panel (KORAK 1: logo top-left) ---
    $header = New-Object System.Windows.Forms.Panel
    $header.Dock = [System.Windows.Forms.DockStyle]::Top
    $header.Height = 64
    $header.BackColor = $Script:Brand.Panel
    $form.Controls.Add($header)

    $logoBox = New-Object System.Windows.Forms.PictureBox
    $logoBox.Size = New-Object System.Drawing.Size(40, 40)
    $logoBox.Location = New-Object System.Drawing.Point(14, 12)
    $logoBox.SizeMode = [System.Windows.Forms.PictureBoxSizeMode]::Zoom
    if (Test-Path $Script:LogoPng) {
        $logoBox.Image = [System.Drawing.Image]::FromFile($Script:LogoPng)
    }
    $header.Controls.Add($logoBox)

    $lblRug = New-Object System.Windows.Forms.Label
    $lblRug.Text = 'RUG'
    $lblRug.Font = New-BrandFont -Kind Display -Size 14 -Style Bold
    $lblRug.ForeColor = $Script:Brand.Cyan
    $lblRug.AutoSize = $true
    $lblRug.Location = New-Object System.Drawing.Point(64, 20)
    $lblRug.BackColor = [System.Drawing.Color]::Transparent
    $header.Controls.Add($lblRug)

    $lblBuster = New-Object System.Windows.Forms.Label
    $lblBuster.Text = 'BUSTER SHIELD'
    $lblBuster.Font = New-BrandFont -Kind Display -Size 14 -Style Bold
    $lblBuster.ForeColor = $Script:Brand.Pink
    $lblBuster.AutoSize = $true
    # NOTE: arithmetic must be resolved into a plain variable first - PowerShell's
    # "New-Object Type(args)" shorthand fails with a MethodNotFound/op_Addition
    # error when an expression (not a bare literal/variable) appears inside the
    # parens, because the parenthesized text is parsed as a literal argument list,
    # not a general expression.
    $lblBusterX = 64 + $lblRug.PreferredWidth + 2
    $lblBuster.Location = New-Object System.Drawing.Point($lblBusterX, 20)
    $lblBuster.BackColor = [System.Drawing.Color]::Transparent
    $header.Controls.Add($lblBuster)

    $lblStatus = New-Object System.Windows.Forms.Label
    $lblStatus.Text = 'Scanning...'
    $lblStatus.Font = New-BrandFont -Kind Mono -Size 9
    $lblStatus.ForeColor = $Script:Brand.TextMuted
    $lblStatus.AutoSize = $true
    $lblStatus.Anchor = [System.Windows.Forms.AnchorStyles]::Top -bor [System.Windows.Forms.AnchorStyles]::Right
    $lblStatus.Location = New-Object System.Drawing.Point(($form.ClientSize.Width - 320), 24)
    $header.Controls.Add($lblStatus)
    $Script:LblStatus = $lblStatus

    # --- Tabs ---
    $tabs = New-Object System.Windows.Forms.TabControl
    $tabs.Dock = [System.Windows.Forms.DockStyle]::Fill
    $tabs.Font = New-BrandFont -Kind Body -Size 9.5
    $tabs.BackColor = $Script:Brand.Panel
    # TabControl's tab strip is OS-chrome-drawn (light grey) by default, same
    # "classic Windows program" clash as the old title bar - OwnerDrawFixed +
    # a DrawItem handler is the only way to actually recolor the tab headers
    # themselves instead of just the pages behind them.
    $tabs.DrawMode = [System.Windows.Forms.TabDrawMode]::OwnerDrawFixed
    $tabs.Add_DrawItem({
        param($s, $e)
        $tabPage = $s.TabPages[$e.Index]
        $isSelected = ($e.Index -eq $s.SelectedIndex)
        $bg = if ($isSelected) { $Script:Brand.Card } else { $Script:Brand.Panel }
        $fg = if ($isSelected) { $Script:Brand.Cyan } else { $Script:Brand.TextMuted }
        $brush = New-Object System.Drawing.SolidBrush($bg)
        $e.Graphics.FillRectangle($brush, $e.Bounds)
        $brush.Dispose()
        $sf = New-Object System.Drawing.StringFormat
        $sf.Alignment = [System.Drawing.StringAlignment]::Center
        $sf.LineAlignment = [System.Drawing.StringAlignment]::Center
        $textBrush = New-Object System.Drawing.SolidBrush($fg)
        # Graphics.DrawString is overloaded for both (RectangleF, StringFormat) and
        # (PointF, StringFormat) - PowerShell's method-overload resolution picked
        # the PointF one for a bare Rectangle argument and then failed trying to
        # convert it, crashing with "Cannot convert argument 'point'...to
        # System.Drawing.PointF". Cast explicitly to force the RectangleF overload.
        $e.Graphics.DrawString($tabPage.Text, $s.Font, $textBrush, [System.Drawing.RectangleF]$e.Bounds, $sf)
        $textBrush.Dispose()
        $sf.Dispose()
    })
    $form.Controls.Add($tabs)
    $tabs.BringToFront()

    $tabLive = New-Object System.Windows.Forms.TabPage
    $tabLive.Name = 'LiveMonitor'
    $tabLive.Text = 'Live Monitor'
    $tabLive.BackColor = $Script:Brand.BgDark
    $tabs.TabPages.Add($tabLive)

    $tabHistory = New-Object System.Windows.Forms.TabPage
    $tabHistory.Name = 'AlertHistory'
    $tabHistory.Text = 'Alert History'
    $tabHistory.BackColor = $Script:Brand.BgDark
    $tabs.TabPages.Add($tabHistory)

    # --- Live grid ---
    $gridLive = New-Object System.Windows.Forms.DataGridView
    $gridLive.Dock = [System.Windows.Forms.DockStyle]::Fill
    $gridLive.ScrollBars = [System.Windows.Forms.ScrollBars]::None
    Set-BrandGridStyle -Grid $gridLive
    foreach ($col in 'ProcessName', 'Pid', 'RemoteHost', 'RemoteAddress', 'RemotePort', 'Signed', 'Severity', 'Timestamp') {
        [void]$gridLive.Columns.Add($col, $col)
    }
    $Script:VScrollLive = New-DarkScrollBar
    $tabLive.Controls.Add($Script:VScrollLive)
    $tabLive.Controls.Add($gridLive)
    $Script:GridLive = $gridLive
    Register-GridScrollSync -Grid $Script:GridLive -ScrollBar $Script:VScrollLive

    # --- History grid + actions ---
    $historyPanel = New-Object System.Windows.Forms.Panel
    $historyPanel.Dock = [System.Windows.Forms.DockStyle]::Fill
    $tabHistory.Controls.Add($historyPanel)

    $actionBar = New-Object System.Windows.Forms.Panel
    $actionBar.Dock = [System.Windows.Forms.DockStyle]::Bottom
    $actionBar.Height = 44
    $actionBar.BackColor = $Script:Brand.Panel
    $historyPanel.Controls.Add($actionBar)

    $btnFalseAlarm = New-BrandButton -Text 'False Alarm' -Accent $Script:Brand.Green
    $btnFalseAlarm.Location = New-Object System.Drawing.Point(12, 7)
    $btnFalseAlarm.Width = 150
    $actionBar.Controls.Add($btnFalseAlarm)

    $btnInvestigated = New-BrandButton -Text 'Investigated - OK' -Accent $Script:Brand.Cyan
    $btnInvestigated.Location = New-Object System.Drawing.Point(172, 7)
    $btnInvestigated.Width = 150
    $actionBar.Controls.Add($btnInvestigated)

    $gridHistory = New-Object System.Windows.Forms.DataGridView
    $gridHistory.Dock = [System.Windows.Forms.DockStyle]::Fill
    $gridHistory.ScrollBars = [System.Windows.Forms.ScrollBars]::None
    Set-BrandGridStyle -Grid $gridHistory
    foreach ($col in 'Timestamp', 'ProcessName', 'RemoteHost', 'Severity', 'Reasons', 'Signed', 'Status', 'Id') {
        [void]$gridHistory.Columns.Add($col, $col)
    }
    $gridHistory.Columns['Id'].Visible = $false
    $Script:VScrollHistory = New-DarkScrollBar
    $historyPanel.Controls.Add($Script:VScrollHistory)
    $historyPanel.Controls.Add($gridHistory)
    $gridHistory.BringToFront()
    $Script:GridHistory = $gridHistory
    Register-GridScrollSync -Grid $Script:GridHistory -ScrollBar $Script:VScrollHistory

    $btnFalseAlarm.Add_Click({
        foreach ($r in $Script:GridHistory.SelectedRows) {
            $procName = $r.Cells['ProcessName'].Value
            $id = $r.Cells['Id'].Value
            if ($procName) { Add-ToWhitelist -ProcessName $procName }
            Set-AlertStatus -Id $id -Status 'False Alarm'
        }
        Refresh-HistoryGrid
    })

    $btnInvestigated.Add_Click({
        foreach ($r in $Script:GridHistory.SelectedRows) {
            $id = $r.Cells['Id'].Value
            Set-AlertStatus -Id $id -Status 'Investigated - OK'
        }
        Refresh-HistoryGrid
    })

    # --- Tray icon (KORAK 1) ---
    $tray = New-Object System.Windows.Forms.NotifyIcon
    if (Test-Path $Script:LogoIco) {
        $tray.Icon = New-Object System.Drawing.Icon($Script:LogoIco)
    }
    $tray.Text = 'RugBuster Shield'
    $tray.Visible = $true

    $menu = New-Object System.Windows.Forms.ContextMenuStrip
    [void]$menu.Items.Add('Open', $null, { Show-MainWindow })
    [void]$menu.Items.Add('Scan Now', $null, { Invoke-RugBusterScan })
    [void]$menu.Items.Add((New-Object System.Windows.Forms.ToolStripSeparator))
    [void]$menu.Items.Add('Exit', $null, {
        $Script:ForceExit = $true
        $Script:TrayIcon.Visible = $false
        [System.Windows.Forms.Application]::Exit()
    })
    $tray.ContextMenuStrip = $menu
    $tray.Add_DoubleClick({ Show-MainWindow })
    $Script:TrayIcon = $tray

    $form.Add_FormClosing({
        param($s, $e)
        # Use the event's own $s (sender = this form), not the outer $form
        # variable: Build-MainForm's local scope is gone by the time this
        # handler actually fires (same issue as the scan timer below), so a
        # closure over the local $form silently resolved to $null, meaning
        # clicking the window's X neither closed nor hid the window.
        if (-not $Script:ForceExit) {
            $e.Cancel = $true
            $s.Hide()
        }
    })

    # --- Scan timer (KORAK 4) ---
    $timer = New-Object System.Windows.Forms.Timer
    $timer.Interval = $Script:Config.CurrentInterval
    # $Script:ScanTimer must be assigned BEFORE Add_Tick is registered, and the
    # handler must reference $Script:ScanTimer rather than the local $timer:
    # Build-MainForm's local scope is gone by the time Application.Run actually
    # pumps a tick, so a scriptblock closing over a function-local variable
    # (not $script:/$global:-scoped) sees $null here - which silently broke the
    # adaptive 5s/10s backoff entirely (every $timer.Interval read/write below
    # was a no-op against nothing, so the interval never actually changed).
    $Script:ScanTimer = $timer
    $timer.Add_Tick({
        Invoke-RugBusterScan
        if ($Script:ScanTimer.Interval -ne $Script:Config.CurrentInterval) {
            $Script:ScanTimer.Interval = $Script:Config.CurrentInterval
        }
        $Script:LblStatus.Text = "Last scan: $(Get-Date -Format 'HH:mm:ss')  |  interval: $($Script:ScanTimer.Interval)ms  |  alerts: $($Script:AlertHistory.Count)"
    })
    $timer.Start()

    # --- Pipe-queue drain timer (toast button -> bring window to front) ---
    $pipeTimer = New-Object System.Windows.Forms.Timer
    $pipeTimer.Interval = 500
    $pipeTimer.Add_Tick({ Process-PipeQueue })
    $pipeTimer.Start()
    $Script:PipeTimer = $pipeTimer

    return $form
}

# DataGridView doesn't create a real native scrollbar child window - it paints
# its own scroll thumb/track directly via GDI+ inside its single HWND (verified
# via EnumChildWindows returning nothing), so SetWindowTheme has nothing to
# theme and the bar stays OS-default light grey no matter what. A plain
# VScrollBar, by contrast, *is* a real native "ScrollBar"-class window, so it
# can be dark-themed - disable the grid's own scrollbar and drive the grid's
# scroll position from a themed VScrollBar sitting next to it instead.
function New-DarkScrollBar {
    $sb = New-Object System.Windows.Forms.VScrollBar
    $sb.Dock = [System.Windows.Forms.DockStyle]::Right
    $sb.Width = 16
    [void][RugBusterShield.ScrollBarTheme]::ApplyDark($sb.Handle)
    return $sb
}

function Sync-GridScrollBar {
    param(
        [Parameter(Mandatory)][System.Windows.Forms.DataGridView]$Grid,
        [Parameter(Mandatory)][System.Windows.Forms.VScrollBar]$ScrollBar
    )
    $displayed = [Math]::Max(1, $Grid.DisplayedRowCount($false))
    if ($Grid.RowCount -le $displayed) {
        $ScrollBar.Visible = $false
        return
    }
    $ScrollBar.Visible = $true
    $ScrollBar.Minimum = 0
    $ScrollBar.Maximum = $Grid.RowCount - 1
    $ScrollBar.LargeChange = $displayed
    $ScrollBar.SmallChange = 1
    $maxFirstRow = $ScrollBar.Maximum - $ScrollBar.LargeChange + 1
    if ($ScrollBar.Value -gt $maxFirstRow) { $ScrollBar.Value = [Math]::Max(0, $maxFirstRow) }
}

# Keeps the DataGridView's actual scroll position and the standalone
# VScrollBar next to it in sync in both directions (dragging the bar moves the
# grid; wheel/keyboard-scrolling the grid moves the bar) without either side
# re-triggering the other via $Script:SyncingScroll.
function Register-GridScrollSync {
    param(
        [Parameter(Mandatory)][System.Windows.Forms.DataGridView]$Grid,
        [Parameter(Mandatory)][System.Windows.Forms.VScrollBar]$ScrollBar
    )
    $ScrollBar.Add_ValueChanged({
        if ($Script:SyncingScroll) { return }
        $Script:SyncingScroll = $true
        try {
            if ($Grid.RowCount -gt 0) {
                $maxRow = $Grid.RowCount - 1
                $Grid.FirstDisplayedScrollingRowIndex = [Math]::Min($ScrollBar.Value, $maxRow)
            }
        } catch { } finally { $Script:SyncingScroll = $false }
    }.GetNewClosure())
    $Grid.Add_Scroll({
        param($s, $e)
        if ($e.ScrollOrientation -ne [System.Windows.Forms.ScrollOrientation]::VerticalScroll) { return }
        if ($Script:SyncingScroll) { return }
        $Script:SyncingScroll = $true
        try {
            $maxFirstRow = [Math]::Max(0, $ScrollBar.Maximum - $ScrollBar.LargeChange + 1)
            $ScrollBar.Value = [Math]::Min([Math]::Max($e.NewValue, 0), $maxFirstRow)
        } catch { } finally { $Script:SyncingScroll = $false }
    }.GetNewClosure())
    $Grid.Add_Resize({ Sync-GridScrollBar -Grid $Grid -ScrollBar $ScrollBar }.GetNewClosure())
}

function Set-BrandGridStyle {
    param([System.Windows.Forms.DataGridView]$Grid)
    $Grid.BackgroundColor = $Script:Brand.BgDark
    $Grid.GridColor = $Script:Brand.GridLine
    $Grid.BorderStyle = [System.Windows.Forms.BorderStyle]::None
    $Grid.RowHeadersVisible = $false
    $Grid.AllowUserToAddRows = $false
    $Grid.AllowUserToDeleteRows = $false
    $Grid.ReadOnly = $true
    $Grid.SelectionMode = [System.Windows.Forms.DataGridViewSelectionMode]::FullRowSelect
    $Grid.AutoSizeColumnsMode = [System.Windows.Forms.DataGridViewAutoSizeColumnsMode]::Fill
    $Grid.Font = New-BrandFont -Kind Mono -Size 9
    $Grid.ColumnHeadersDefaultCellStyle.BackColor = $Script:Brand.Panel
    $Grid.ColumnHeadersDefaultCellStyle.ForeColor = $Script:Brand.Cyan
    $Grid.ColumnHeadersDefaultCellStyle.Font = New-BrandFont -Kind Body -Size 9 -Style Bold
    $Grid.DefaultCellStyle.BackColor = $Script:Brand.Card
    $Grid.DefaultCellStyle.ForeColor = $Script:Brand.TextPrimary
    $Grid.DefaultCellStyle.SelectionBackColor = $Script:Brand.Panel
    $Grid.DefaultCellStyle.SelectionForeColor = $Script:Brand.Cyan
    $Grid.EnableHeadersVisualStyles = $false
}

function Update-LiveGrid {
    param($Rows)
    if (-not $Script:GridLive) { return }
    $Script:GridLive.Rows.Clear()
    foreach ($r in $Rows) {
        $idx = $Script:GridLive.Rows.Add($r.ProcessName, $r.Pid, $r.RemoteHost, $r.RemoteAddress, $r.RemotePort, $r.Signed, $r.Severity, $r.Timestamp)
        $Script:GridLive.Rows[$idx].DefaultCellStyle.ForeColor = Get-SeverityColor -Severity $r.Severity
    }
    if ($Script:VScrollLive) { Sync-GridScrollBar -Grid $Script:GridLive -ScrollBar $Script:VScrollLive }
}

function Refresh-HistoryGrid {
    if (-not $Script:GridHistory) { return }
    $Script:GridHistory.Rows.Clear()
    foreach ($r in $Script:AlertHistory) {
        $idx = $Script:GridHistory.Rows.Add($r.Timestamp, $r.ProcessName, $r.RemoteHost, $r.Severity, $r.Reasons, $r.Signed, $r.Status, $r.Id)
        $Script:GridHistory.Rows[$idx].DefaultCellStyle.ForeColor = Get-SeverityColor -Severity $r.Severity
    }
    if ($Script:VScrollHistory) { Sync-GridScrollBar -Grid $Script:GridHistory -ScrollBar $Script:VScrollHistory }
}

function Set-AlertStatus {
    param([string]$Id, [string]$Status)
    foreach ($a in $Script:AlertHistory) {
        if ($a.Id -eq $Id) { $a.Status = $Status }
    }
    Save-AlertHistory
}

function Show-MainWindow {
    $Script:MainForm.Show()
    $Script:MainForm.WindowState = [System.Windows.Forms.FormWindowState]::Normal
    $Script:MainForm.Activate()
    $Script:MainForm.BringToFront()
}

function Select-HistoryRowById {
    param([string]$Id)
    Refresh-HistoryGrid
    foreach ($row in $Script:GridHistory.Rows) {
        if ($row.Cells['Id'].Value -eq $Id) {
            $Script:MainTabs.SelectedTab = $Script:MainTabs.TabPages['AlertHistory']
            $row.Selected = $true
            $Script:GridHistory.FirstDisplayedScrollingRowIndex = $row.Index
            break
        }
    }
}

function Process-PipeQueue {
    $line = $null
    while ($Script:PipeQueue.TryDequeue([ref]$line)) {
        Handle-RugBusterUri -Uri $line
    }
    if ($Script:PendingSignal) {
        $sig = $Script:PendingSignal
        $Script:PendingSignal = $null
        Handle-RugBusterUri -Uri $sig
    }
}

function Handle-RugBusterUri {
    param([string]$Uri)
    if (-not $Uri) { return }
    # Format: rugbuster-shield:<action>?id=<guid>
    $body = $Uri -replace '^rugbuster-shield:', ''
    $parts = $body -split '\?id=', 2
    $action = $parts[0]
    $id = if ($parts.Count -gt 1) { $parts[1] } else { $null }

    Show-MainWindow
    switch ($action) {
        'show' { if ($id) { Select-HistoryRowById -Id $id } }
        'falsealarm' {
            if ($id) {
                $a = $Script:AlertHistory | Where-Object { $_.Id -eq $id } | Select-Object -First 1
                if ($a) { Add-ToWhitelist -ProcessName $a.ProcessName }
                Set-AlertStatus -Id $id -Status 'False Alarm'
            }
            Select-HistoryRowById -Id $id
        }
        'investigated' {
            if ($id) { Set-AlertStatus -Id $id -Status 'Investigated - OK' }
            Select-HistoryRowById -Id $id
        }
    }
}

#endregion

# ============================================================================
#region MAIN
# ============================================================================

Import-Whitelist
Import-SeenState
Import-AlertHistory

# --- Single-instance guard --------------------------------------------
# A toast "Details"/"False Alarm"/"Investigated - OK" click launches a
# brand-new process (via the registered rugbuster-shield: protocol command)
# whose only job is to relay the click to the already-running instance over
# the named pipe and exit. The old code inferred "is an instance already
# running?" purely from whether Send-RugBusterSignal's pipe-connect
# succeeded within its timeout - on a loaded machine that raced and lost
# often enough that a failed send fell through into building an entire
# SECOND GUI window, which is exactly the "a window flashes up and vanishes"
# behavior this was reported as: two overlapping "RugBuster Shield" windows
# both centered on screen, one hiding behind the other. A named Mutex is a
# reliable, non-racy "is an instance already running" check.
$Script:SingleInstanceMutex = New-Object System.Threading.Mutex($false, 'Local\RugBusterShieldSingleInstance')
$Script:IsFirstInstance = $Script:SingleInstanceMutex.WaitOne(0)

if (-not $Script:IsFirstInstance) {
    # Another instance owns the mutex - relay the click (or just ask it to
    # come forward) over the pipe and exit. Never build a second GUI here.
    $payload = if ($Signal) { $Signal } else { 'rugbuster-shield:show' }
    [void](Send-RugBusterSignal -Payload $payload)
    exit 0
}

# We're the sole instance. If we were still launched with a signal (e.g. a
# toast click racing an instance that was in the middle of shutting down, so
# the mutex was free by the time we checked), just remember it and act on it
# once our own window is up - Process-PipeQueue picks up $PendingSignal.
if ($Signal) {
    $Script:PendingSignal = $Signal
}

Register-RugBusterProtocol
Start-RugBusterPipeServer

$Script:ForceExit = $false
$Script:MainForm = Build-MainForm
$Script:MainTabs = $Script:MainForm.Controls | Where-Object { $_ -is [System.Windows.Forms.TabControl] } | Select-Object -First 1

Refresh-HistoryGrid
Invoke-RugBusterScan

[System.Windows.Forms.Application]::Run($Script:MainForm)

#endregion
