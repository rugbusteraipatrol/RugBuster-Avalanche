<#
    RugBuster Shield GUI
    ---------------------
    Windows desktop companion (PowerShell + WinForms) that watches running
    processes and their outbound TCP connections, scores each one with a
    3-level severity model, and raises Windows 11 toast notifications
    (Viber/Telegram style, bottom-right) through the BurntToast module.

    BRANDING SOURCE (extracted 2026-08-13 from the real rugbuster.io site,
    repo rugbusteraipatrol/rugbuster-website, index.html <style>):
      - Wordmark: "RUG" + "BUSTER" plain text, no image/SVG logo exists on
        the live site -- it is CSS text with a neon glow. This script
        therefore *generates* the tray/header icon at runtime (see
        New-RugBusterIcon) instead of copying a nonexistent asset.
      - Colors (CSS custom properties from rugbuster.io):
          --neon-cyan:   #00F5FF
          --neon-pink:   #FF00C8
          --neon-green:  #00FF88
          --neon-orange: #FF6B00
          danger red:    #FF3D3D   (used for .t-danger / .r-danger)
          --dark-bg:     #050811
          --dark-panel:  #0A0F1E
          --dark-card:   #0D1527
          --text-primary:#E8F4FF
          --text-muted:  #5A7A9A
      - Fonts: "Orbitron" (headings/logo), "Rajdhani" (body),
        "Share Tech Mono" (labels/mono/terminal). These are Google Fonts;
        WinForms cannot fetch web fonts, so Get-RugBusterFont below picks
        the real family only if it is installed locally and otherwise
        falls back to Consolas (keeps the terminal/hacker look) or
        Segoe UI. Install the three families from fonts.google.com on the
        host machine for a pixel-accurate look.
      - Visual language: dark background, angular clipped corners (no
        border-radius on rugbuster.io — unlike the rounded glass style of
        the Avalanche console UI in this same repo), neon glow shadows,
        scanline/grid texture. This script keeps flat dark panels with a
        1px neon-cyan hairline border to approximate that without a WPF
        renderer.

    ENVIRONMENT NOTE: this file was authored in a Linux cloud container
    with no Windows, no WinForms runtime, and no BurntToast module
    available, so it could not be executed or visually verified here.
    Run it on a real Windows 10/11 host with PowerShell 5.1+ before
    relying on it; see windows/README.md for the test checklist.
#>

[CmdletBinding()]
param(
    # Set by the registered rugbuster: URI protocol handler when a user
    # clicks "Detalji" on a toast raised by an already-running instance.
    [string]$FocusAlertId,

    # Start hidden in the system tray instead of showing the main window.
    [switch]$MinimizedStart
)

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[System.Windows.Forms.Application]::EnableVisualStyles()

#region ---------------------------------------------------------------- Configuration
$Script:Config = @{
    ScriptDir           = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }
    IntervalMsFast       = 5000          # KORAK 4: default scan cadence (5s)
    IntervalMsSlow       = 10000         # fallback cadence if scans are too expensive
    CpuBudgetRatio       = 0.5           # if a scan takes > 50% of the interval, drop to the slow cadence
    NewFileWindowHours   = 24            # HIGH rule (b): file written within this many hours
    EnableBurstHeuristic = $false        # HIGH rule (c): optional, off by default (see Test-RugBusterBurst)
    BurstBytesThreshold  = 5MB
    BurstWindowSeconds   = 30
    DnsTimeoutMs         = 300
    ProtocolScheme       = 'rugbuster'
}
$Script:Config.WhitelistPath = Join-Path $Script:Config.ScriptDir 'rugbuster_whitelist.txt'
$Script:Config.StatePath     = Join-Path $Script:Config.ScriptDir 'rugbuster_state.json'
$Script:Config.HistoryPath   = Join-Path $Script:Config.ScriptDir 'rugbuster_alert_history.json'
$Script:Config.IconIcoPath   = Join-Path $Script:Config.ScriptDir 'rugbuster_logo.ico'
$Script:Config.IconPngPath   = Join-Path $Script:Config.ScriptDir 'rugbuster_logo.png'
$Script:Config.SignalPath    = Join-Path $Script:Config.ScriptDir 'rugbuster_focus_signal.txt'

$Script:Brand = @{
    NeonCyan    = [System.Drawing.ColorTranslator]::FromHtml('#00F5FF')
    NeonPink    = [System.Drawing.ColorTranslator]::FromHtml('#FF00C8')
    NeonGreen   = [System.Drawing.ColorTranslator]::FromHtml('#00FF88')
    NeonOrange  = [System.Drawing.ColorTranslator]::FromHtml('#FF6B00')
    DangerRed   = [System.Drawing.ColorTranslator]::FromHtml('#FF3D3D')
    DarkBg      = [System.Drawing.ColorTranslator]::FromHtml('#050811')
    DarkPanel   = [System.Drawing.ColorTranslator]::FromHtml('#0A0F1E')
    DarkCard    = [System.Drawing.ColorTranslator]::FromHtml('#0D1527')
    TextPrimary = [System.Drawing.ColorTranslator]::FromHtml('#E8F4FF')
    TextMuted   = [System.Drawing.ColorTranslator]::FromHtml('#5A7A9A')
}
#endregion

#region ---------------------------------------------------------------- Fonts
function Get-RugBusterFont {
    <#
        Returns the real rugbuster.io family when it is installed locally,
        otherwise a safe fallback that keeps the terminal aesthetic.
    #>
    param(
        [ValidateSet('Heading', 'Body', 'Mono')]
        [string]$Role,
        [single]$Size = 10,
        [System.Drawing.FontStyle]$Style = [System.Drawing.FontStyle]::Regular
    )

    $preferred = switch ($Role) {
        'Heading' { 'Orbitron' }
        'Body'    { 'Rajdhani' }
        'Mono'    { 'Share Tech Mono' }
    }
    $fallback = switch ($Role) {
        'Heading' { 'Consolas' }
        'Body'    { 'Segoe UI' }
        'Mono'    { 'Consolas' }
    }

    $installed = [System.Drawing.FontFamily]::Families.Name
    $family = if ($installed -contains $preferred) { $preferred } else { $fallback }
    return New-Object System.Drawing.Font($family, $Size, $Style)
}
#endregion

#region ---------------------------------------------------------------- Icon / logo generation
function New-RugBusterIcon {
    <#
        rugbuster.io has no logo image asset (its wordmark is styled text),
        so the shield icon used for the header, tray, and toast AppLogo is
        drawn here at runtime in brand colors and cached to disk. Delete
        the cached files to force a redraw after changing the palette.
    #>
    param([int]$Size = 64)

    if ((Test-Path $Script:Config.IconIcoPath) -and (Test-Path $Script:Config.IconPngPath)) {
        return
    }

    $bmp = New-Object System.Drawing.Bitmap($Size, $Size)
    $gfx = [System.Drawing.Graphics]::FromImage($bmp)
    $gfx.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
    $gfx.Clear([System.Drawing.Color]::Transparent)

    # Shield outline
    $shield = New-Object System.Drawing.Drawing2D.GraphicsPath
    $w = $Size; $h = $Size
    $points = @(
        [System.Drawing.PointF]::new($w * 0.5, $h * 0.04),
        [System.Drawing.PointF]::new($w * 0.92, $h * 0.20),
        [System.Drawing.PointF]::new($w * 0.92, $h * 0.52),
        [System.Drawing.PointF]::new($w * 0.5,  $h * 0.97),
        [System.Drawing.PointF]::new($w * 0.08, $h * 0.52),
        [System.Drawing.PointF]::new($w * 0.08, $h * 0.20)
    )
    $shield.AddPolygon($points)

    $bgBrush = New-Object System.Drawing.SolidBrush($Script:Brand.DarkBg)
    $gfx.FillPath($bgBrush, $shield)

    $pen = New-Object System.Drawing.Pen($Script:Brand.NeonCyan, 3)
    $gfx.DrawPath($pen, $shield)

    $font = New-Object System.Drawing.Font('Consolas', [single]($Size * 0.34), [System.Drawing.FontStyle]::Bold)
    $textBrush = New-Object System.Drawing.SolidBrush($Script:Brand.NeonCyan)
    $format = New-Object System.Drawing.StringFormat
    $format.Alignment = [System.Drawing.StringAlignment]::Center
    $format.LineAlignment = [System.Drawing.StringAlignment]::Center
    $gfx.DrawString('RB', $font, $textBrush, [System.Drawing.RectangleF]::new(0, $h * 0.06, $w, $h * 0.86), $format)

    $gfx.Dispose()
    $bmp.Save($Script:Config.IconPngPath, [System.Drawing.Imaging.ImageFormat]::Png)

    $hIcon = $bmp.GetHicon()
    $icon = [System.Drawing.Icon]::FromHandle($hIcon)
    $fs = [System.IO.File]::Create($Script:Config.IconIcoPath)
    $icon.Save($fs)
    $fs.Close()
    $icon.Dispose()
    $bmp.Dispose()
}
#endregion

#region ---------------------------------------------------------------- Whitelist (KORAK 5)
function Initialize-RugBusterWhitelist {
    if (Test-Path $Script:Config.WhitelistPath) { return }
    @'
# rugbuster_whitelist.txt
# One process name per line (with or without .exe), case-insensitive.
# Lines starting with # are comments and are ignored.
# Processes on this list are always treated as LOW severity (log-only)
# and will never raise a MEDIUM/HIGH ("worm"/"danger") alert, per KORAK 5.
# Add an entry from the "Istorija upozorenja" tab with "Lazna uzbuna",
# or edit this file by hand and restart the app / wait for the next reload.

# Browsers
chrome
msedge
firefox
brave
opera
msedgewebview2

# OS / update / shell
svchost
wuauclt
usoclient
explorer
searchapp
searchhost
onedrive
dllhost
taskhostw

# Chat / collaboration
discord
discordptb
discordcanary
teams
ms-teams
slack
skype
zoom
whatsapp

# Gaming / media
steam
steamwebhelper
steamservice
spotify

# Dev tools
code
git
'@ | Set-Content -Path $Script:Config.WhitelistPath -Encoding UTF8
}

function Get-RugBusterWhitelist {
    Initialize-RugBusterWhitelist
    $lines = Get-Content -Path $Script:Config.WhitelistPath -ErrorAction SilentlyContinue
    $set = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::OrdinalIgnoreCase)
    foreach ($line in $lines) {
        $trimmed = $line.Trim()
        if (-not $trimmed -or $trimmed.StartsWith('#')) { continue }
        $null = $set.Add(($trimmed -replace '\.exe$', ''))
    }
    # The leading comma is required: without it PowerShell enumerates
    # (unrolls) the HashSet onto the output stream instead of returning it
    # as one object, which silently turns a 0- or 1-entry set into $null
    # or a bare string at the call site.
    return ,$set
}

function Add-RugBusterWhitelistEntry {
    param([Parameter(Mandatory)][string]$ProcessName)
    $name = $ProcessName -replace '\.exe$', ''
    if ($Script:WhitelistSet.Contains($name)) { return }
    Add-Content -Path $Script:Config.WhitelistPath -Value $name -Encoding UTF8
    $Script:WhitelistSet = Get-RugBusterWhitelist
}

function Test-RugBusterWhitelisted {
    param([string]$ProcessName)
    $name = $ProcessName -replace '\.exe$', ''
    return $Script:WhitelistSet.Contains($name)
}
#endregion

#region ---------------------------------------------------------------- State (seen processes / destinations)
function Import-RugBusterState {
    if (Test-Path $Script:Config.StatePath) {
        try {
            $raw = Get-Content -Path $Script:Config.StatePath -Raw | ConvertFrom-Json
            $seenProc = @{}
            $raw.SeenProcesses.PSObject.Properties | ForEach-Object { $seenProc[$_.Name] = $_.Value }
            $seenPairs = @{}
            $raw.SeenPairs.PSObject.Properties | ForEach-Object { $seenPairs[$_.Name] = $_.Value }
            return @{ SeenProcesses = $seenProc; SeenPairs = $seenPairs }
        } catch {
            Write-Warning "RugBuster: state file was unreadable, starting fresh ($($_.Exception.Message))"
        }
    }
    return @{ SeenProcesses = @{}; SeenPairs = @{} }
}

function Save-RugBusterState {
    $Script:State | ConvertTo-Json -Depth 5 | Set-Content -Path $Script:Config.StatePath -Encoding UTF8
}
#endregion

#region ---------------------------------------------------------------- Alert history (KORAK 6)
function Import-RugBusterHistory {
    # The leading comma on both returns is required: without it PowerShell
    # enumerates (unrolls) the ArrayList onto the output stream instead of
    # returning it as one object, which silently turns an empty or
    # single-entry history into $null or a bare object at the call site.
    if (Test-Path $Script:Config.HistoryPath) {
        try {
            $items = @(Get-Content -Path $Script:Config.HistoryPath -Raw | ConvertFrom-Json)
            return ,[System.Collections.ArrayList]::new(@($items))
        } catch {
            Write-Warning "RugBuster: history file was unreadable, starting fresh ($($_.Exception.Message))"
        }
    }
    return ,[System.Collections.ArrayList]::new()
}

function Save-RugBusterHistory {
    $Script:AlertHistory | ConvertTo-Json -Depth 5 | Set-Content -Path $Script:Config.HistoryPath -Encoding UTF8
}

function Add-RugBusterHistoryEntry {
    # Note: the parameter is named $ProcessId, not $Pid -- $Pid is a
    # read-only PowerShell automatic variable (current process id) and
    # cannot be used as a parameter/local variable name.
    param($ProcessName, $ProcessId, $Destination, $Severity, [string[]]$Reasons)
    $entry = [pscustomobject]@{
        Id          = [guid]::NewGuid().ToString('N')
        Timestamp   = (Get-Date).ToString('o')
        ProcessName = $ProcessName
        Pid         = $ProcessId
        Destination = $Destination
        Severity    = $Severity
        Reasons     = ($Reasons -join '; ')
        Status      = 'Neregistrovano'   # Neregistrovano | Lazna uzbuna | Istrazeno - OK
    }
    [void]$Script:AlertHistory.Add($entry)
    Save-RugBusterHistory
    return $entry
}

function Set-RugBusterAlertStatus {
    param([Parameter(Mandatory)][string]$AlertId, [Parameter(Mandatory)][string]$Status)
    $entry = $Script:AlertHistory | Where-Object { $_.Id -eq $AlertId } | Select-Object -First 1
    if (-not $entry) { return }
    $entry.Status = $Status
    if ($Status -eq 'Lazna uzbuna') {
        Add-RugBusterWhitelistEntry -ProcessName $entry.ProcessName
    }
    Save-RugBusterHistory
    Update-RugBusterHistoryGrid
}
#endregion

#region ---------------------------------------------------------------- Signature / new-file / burst checks (KORAK 3)
function Get-RugBusterSignatureStatus {
    param([string]$Path)
    if (-not $Path -or -not (Test-Path -LiteralPath $Path)) { return 'Unknown' }
    try {
        return (Get-AuthenticodeSignature -FilePath $Path).Status.ToString()
    } catch {
        return 'Unknown'
    }
}

function Test-RugBusterRecentlyWritten {
    <#
        HIGH rule (b): binary appeared / changed within the last
        $Config.NewFileWindowHours hours and is opening an external
        connection right now. Task text names LastWriteTime explicitly;
        CreationTime is also checked (and the more recent of the two is
        used) so a freshly-dropped-and-touched dropper is still caught.
    #>
    param([string]$Path)
    if (-not $Path -or -not (Test-Path -LiteralPath $Path)) { return $false }
    $item = Get-Item -LiteralPath $Path -ErrorAction SilentlyContinue
    if (-not $item) { return $false }
    $mostRecent = if ($item.LastWriteTimeUtc -gt $item.CreationTimeUtc) { $item.LastWriteTimeUtc } else { $item.CreationTimeUtc }
    return ((Get-Date).ToUniversalTime() - $mostRecent).TotalHours -lt $Script:Config.NewFileWindowHours
}

function Test-RugBusterBurst {
    <#
        HIGH rule (c), optional/best-effort per the task spec ("preskoci
        ako je previse kompleksno za real-time"). Windows exposes
        per-connection byte counters only through performance counters,
        not Get-NetTCPConnection, and mapping a counter instance back to
        a specific remote endpoint is unreliable for processes with many
        sockets. This is therefore disabled by default
        ($Config.EnableBurstHeuristic = $false); flip it on only if you
        have verified the "Process(<name>)\IO Data Bytes/sec" counter is
        available and acceptably cheap to poll on the target machine.
    #>
    param([string]$ProcessName)
    if (-not $Script:Config.EnableBurstHeuristic) { return $false }
    try {
        $counterPath = "\Process($ProcessName)\IO Data Bytes/sec"
        $sample = (Get-Counter -Counter $counterPath -ErrorAction Stop).CounterSamples | Select-Object -First 1
        return ($sample.CookedValue * $Script:Config.BurstWindowSeconds) -gt $Script:Config.BurstBytesThreshold
    } catch {
        return $false
    }
}
#endregion

#region ---------------------------------------------------------------- Reverse DNS (cached, timeout-bounded)
$Script:DnsCache = @{}
function Resolve-RugBusterHost {
    param([string]$IpAddress)
    if ($Script:DnsCache.ContainsKey($IpAddress)) { return $Script:DnsCache[$IpAddress] }
    $resolved = $IpAddress
    try {
        $iar = [System.Net.Dns]::BeginGetHostEntry($IpAddress, $null, $null)
        if ($iar.AsyncWaitHandle.WaitOne($Script:Config.DnsTimeoutMs)) {
            $entry = [System.Net.Dns]::EndGetHostEntry($iar)
            if ($entry.HostName) { $resolved = $entry.HostName }
        }
    } catch {
        # Leave $resolved as the raw IP; PTR lookups routinely fail/time out.
    }
    $Script:DnsCache[$IpAddress] = $resolved
    return $resolved
}
#endregion

#region ---------------------------------------------------------------- Severity engine (KORAK 3)
function Get-RugBusterSeverity {
    <#
        Severity thresholds (documented per task requirement):

        LOW    - whitelisted process, OR a (process, destination) pair
                 already seen before by this app. Logged to the Monitor
                 grid only; never raises a toast or a history entry.

        MEDIUM ("WORM") - not whitelisted, and either:
                   * the process name has never been seen before, OR
                   * the process is known but this destination is new
                 Raises a silent/quiet toast (New-BurntToastNotification
                 -Silent) and a history entry.

        HIGH ("DANGER") - not whitelisted, and any of:
                   (a) Get-AuthenticodeSignature -> NotSigned or
                       HashMismatch
                   (b) the executable was written/created within the last
                       $Config.NewFileWindowHours hours (default 24) and
                       is opening an external connection right now
                   (c) [optional, off by default] a burst of traffic to
                       an unseen destination within
                       $Config.BurstWindowSeconds seconds exceeding
                       $Config.BurstBytesThreshold bytes
                 Raises a loud toast with sound, pinned (Reminder
                 scenario) until the user resolves it from "Istorija
                 upozorenja" with "Lazna uzbuna" or "Istrazeno - OK".

        Whitelisted processes short-circuit to LOW before any other
        check runs, so they can never produce a MEDIUM/HIGH alert
        (KORAK 5 requirement).
    #>
    param(
        [Parameter(Mandatory)][string]$ProcessName,
        [string]$ProcessPath,
        [Parameter(Mandatory)][string]$Destination
    )

    if (Test-RugBusterWhitelisted -ProcessName $ProcessName) {
        return @{ Level = 'LOW'; Reasons = @('Whitelist') }
    }

    $reasons = @()

    $sigStatus = Get-RugBusterSignatureStatus -Path $ProcessPath
    if ($sigStatus -in @('NotSigned', 'HashMismatch')) {
        $reasons += "Nepotpisan ili osteceni potpis ($sigStatus)"
    }

    if (Test-RugBusterRecentlyWritten -Path $ProcessPath) {
        $reasons += "Fajl mladji od $($Script:Config.NewFileWindowHours)h i odmah pravi eksternu konekciju"
    }

    if (Test-RugBusterBurst -ProcessName $ProcessName) {
        $reasons += 'Veci obim podataka ka nepoznatoj destinaciji u kratkom periodu'
    }

    if ($reasons.Count -gt 0) {
        return @{ Level = 'HIGH'; Reasons = $reasons }
    }

    $procKey = $ProcessName.ToLowerInvariant()
    $pairKey = "$procKey|$($Destination.ToLowerInvariant())"
    $isKnownProcess = $Script:State.SeenProcesses.ContainsKey($procKey)
    $isKnownPair = $Script:State.SeenPairs.ContainsKey($pairKey)

    if (-not $isKnownProcess) {
        return @{ Level = 'MEDIUM'; Reasons = @('Nov proces') }
    }
    if (-not $isKnownPair) {
        return @{ Level = 'MEDIUM'; Reasons = @('Nova destinacija za poznat proces') }
    }

    return @{ Level = 'LOW'; Reasons = @('Poznat proces i poznata destinacija') }
}
#endregion

#region ---------------------------------------------------------------- Toast notifications (KORAK 2)
$Script:BurntToastAvailable = $false
function Initialize-RugBusterToast {
    if (Get-Module -ListAvailable -Name BurntToast) {
        try {
            Import-Module BurntToast -ErrorAction Stop
            $Script:BurntToastAvailable = $true
        } catch {
            Write-Warning "RugBuster: BurntToast is installed but failed to import ($($_.Exception.Message)); falling back to tray balloon tips."
        }
    } else {
        Write-Warning "RugBuster: BurntToast module not found. Install it with 'Install-Module BurntToast -Scope CurrentUser' for Windows 11 style toasts; falling back to tray balloon tips until then."
    }
}

function Send-RugBusterToast {
    param(
        [ValidateSet('MEDIUM', 'HIGH')][string]$Severity,
        [string]$ProcessName,
        [string]$Destination,
        [string]$AlertId
    )

    $titleLine = if ($Severity -eq 'HIGH') { 'RUGBUSTER SHIELD - DANGER' } else { 'RugBuster Shield - Worm' }
    $bodyLine = "$ProcessName -> $Destination"

    if (-not $Script:BurntToastAvailable) {
        $icon = if ($Severity -eq 'HIGH') { [System.Windows.Forms.ToolTipIcon]::Error } else { [System.Windows.Forms.ToolTipIcon]::Warning }
        $Script:NotifyIcon.ShowBalloonTip(6000, $titleLine, $bodyLine, $icon)
        return
    }

    $protocolArg = "$($Script:Config.ProtocolScheme):$AlertId"

    try {
        if ($Severity -eq 'HIGH') {
            # Pinned/persistent toast: stays until the user clicks a
            # button (Reminder scenario), with a looping alarm sound.
            $btnDetails = New-BTButton -Content 'Detalji' -Arguments $protocolArg -ActivationType Protocol
            $btnDismiss = New-BTButton -Dismiss -Content 'Odbaci'
            $text1 = New-BTText -Content $titleLine
            $text2 = New-BTText -Content $bodyLine
            $logo = New-BTImage -Source $Script:Config.IconPngPath -AppLogoOverride -Crop Circle
            $binding = New-BTBinding -Children $text1, $text2 -AppLogoOverride $logo
            $visual = New-BTVisual -BindingGeneric $binding
            $audio = New-BTAudio -Source 'ms-winsoundevent:Notification.Looping.Alarm2' -Loop
            $actions = New-BTAction -Buttons $btnDetails, $btnDismiss
            $content = New-BTContent -Visual $visual -Audio $audio -Actions $actions -Scenario Reminder
            Submit-BTNotification -Content $content -UniqueIdentifier $AlertId
        } else {
            $btnDetails = New-BTButton -Content 'Detalji' -Arguments $protocolArg -ActivationType Protocol
            New-BurntToastNotification -Text $titleLine, $bodyLine -AppLogo $Script:Config.IconPngPath `
                -Button $btnDetails -Silent -UniqueIdentifier $AlertId
        }
    } catch {
        Write-Warning "RugBuster: toast via BurntToast failed ($($_.Exception.Message)); this BurntToast version may not support the Reminder scenario. Falling back to a balloon tip."
        $icon = if ($Severity -eq 'HIGH') { [System.Windows.Forms.ToolTipIcon]::Error } else { [System.Windows.Forms.ToolTipIcon]::Warning }
        $Script:NotifyIcon.ShowBalloonTip(6000, $titleLine, $bodyLine, $icon)
    }
}

function Register-RugBusterProtocolHandler {
    <#
        Registers the "rugbuster:" URI scheme under HKCU (no admin rights
        needed) so a toast's "Detalji" button can bring the already
        running app to the front and jump to the right history row, even
        though Windows launches button activations as a brand new
        process.
    #>
    $scheme = $Script:Config.ProtocolScheme
    $scriptPath = $MyInvocation.MyCommand.Path
    if (-not $scriptPath) { $scriptPath = Join-Path $Script:Config.ScriptDir 'RugBuster-Shield-GUI.ps1' }
    $command = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$scriptPath`" -FocusAlertId `"%1`""

    try {
        New-Item -Path "HKCU:\Software\Classes\$scheme" -Force | Out-Null
        Set-ItemProperty -Path "HKCU:\Software\Classes\$scheme" -Name '(Default)' -Value "URL:RugBuster Shield Protocol"
        Set-ItemProperty -Path "HKCU:\Software\Classes\$scheme" -Name 'URL Protocol' -Value ''
        New-Item -Path "HKCU:\Software\Classes\$scheme\shell\open\command" -Force | Out-Null
        Set-ItemProperty -Path "HKCU:\Software\Classes\$scheme\shell\open\command" -Name '(Default)' -Value $command
    } catch {
        Write-Warning "RugBuster: could not register the rugbuster: protocol handler ($($_.Exception.Message)); the toast 'Detalji' button will still open the app but may not focus the right row."
    }
}
#endregion

#region ---------------------------------------------------------------- Connection scan (KORAK 3/4)
function Invoke-RugBusterScan {
    $stopwatch = [System.Diagnostics.Stopwatch]::StartNew()

    try {
        $connections = Get-NetTCPConnection -State Established -ErrorAction SilentlyContinue |
            Where-Object { $_.RemoteAddress -notin @('127.0.0.1', '::1', '0.0.0.0') }
    } catch {
        Write-Warning "RugBuster: Get-NetTCPConnection failed ($($_.Exception.Message))"
        return
    }

    foreach ($conn in $connections) {
        $proc = Get-Process -Id $conn.OwningProcess -ErrorAction SilentlyContinue
        if (-not $proc) { continue }

        $processName = $proc.ProcessName
        $processPath = $proc.Path
        $destinationHost = Resolve-RugBusterHost -IpAddress $conn.RemoteAddress
        $destinationDisplay = if ($destinationHost -ne $conn.RemoteAddress) {
            "$destinationHost ($($conn.RemoteAddress))"
        } else {
            $conn.RemoteAddress
        }

        $result = Get-RugBusterSeverity -ProcessName $processName -ProcessPath $processPath -Destination $destinationHost

        Add-RugBusterMonitorRow -Time (Get-Date) -ProcessName $processName -ProcessId $conn.OwningProcess `
            -Destination $destinationDisplay -Port $conn.RemotePort -SignatureStatus (Get-RugBusterSignatureStatus -Path $processPath) `
            -Severity $result.Level

        $procKey = $processName.ToLowerInvariant()
        $pairKey = "$procKey|$($destinationHost.ToLowerInvariant())"
        $Script:State.SeenProcesses[$procKey] = (Get-Date).ToString('o')
        $Script:State.SeenPairs[$pairKey] = (Get-Date).ToString('o')

        if ($result.Level -in @('MEDIUM', 'HIGH')) {
            $entry = Add-RugBusterHistoryEntry -ProcessName $processName -ProcessId $conn.OwningProcess `
                -Destination $destinationDisplay -Severity $result.Level -Reasons $result.Reasons
            Send-RugBusterToast -Severity $result.Level -ProcessName $processName -Destination $destinationDisplay -AlertId $entry.Id
            Update-RugBusterHistoryGrid
        }
    }

    Save-RugBusterState

    $stopwatch.Stop()
    # KORAK 4: adaptive cadence. If a scan tick eats more than
    # $Config.CpuBudgetRatio of the current interval, this is too heavy
    # for a 5s cadence on this machine, so back off to 10s.
    $budgetMs = $Script:ScanTimer.Interval * $Script:Config.CpuBudgetRatio
    if ($stopwatch.ElapsedMilliseconds -gt $budgetMs -and $Script:ScanTimer.Interval -eq $Script:Config.IntervalMsFast) {
        $Script:ScanTimer.Interval = $Script:Config.IntervalMsSlow
        Write-Warning "RugBuster: scan took $($stopwatch.ElapsedMilliseconds)ms, slowing scan cadence to $($Script:Config.IntervalMsSlow)ms to limit CPU load."
    }
}
#endregion

#region ---------------------------------------------------------------- GUI construction
function New-RugBusterMainForm {
    $form = New-Object System.Windows.Forms.Form
    $form.Text = 'RugBuster Shield'
    $form.Size = New-Object System.Drawing.Size(1040, 660)
    $form.MinimumSize = New-Object System.Drawing.Size(820, 480)
    $form.StartPosition = 'CenterScreen'
    $form.BackColor = $Script:Brand.DarkBg
    $form.ForeColor = $Script:Brand.TextPrimary
    if (Test-Path $Script:Config.IconIcoPath) {
        $form.Icon = New-Object System.Drawing.Icon($Script:Config.IconIcoPath)
    }

    # ---- Header panel (logo top-left) ----
    $header = New-Object System.Windows.Forms.Panel
    $header.Dock = 'Top'
    $header.Height = 72
    $header.BackColor = $Script:Brand.DarkPanel
    $form.Controls.Add($header)

    $logoBox = New-Object System.Windows.Forms.PictureBox
    $logoBox.Location = New-Object System.Drawing.Point(14, 10)
    $logoBox.Size = New-Object System.Drawing.Size(52, 52)
    $logoBox.SizeMode = 'Zoom'
    if (Test-Path $Script:Config.IconPngPath) {
        $logoBox.Image = [System.Drawing.Image]::FromFile($Script:Config.IconPngPath)
    }
    $header.Controls.Add($logoBox)

    $titleLabel = New-Object System.Windows.Forms.Label
    $titleLabel.Text = 'RUGBUSTER SHIELD'
    $titleLabel.Font = Get-RugBusterFont -Role Heading -Size 15 -Style Bold
    $titleLabel.ForeColor = $Script:Brand.NeonCyan
    $titleLabel.AutoSize = $true
    $titleLabel.Location = New-Object System.Drawing.Point(78, 12)
    $header.Controls.Add($titleLabel)

    $subtitleLabel = New-Object System.Windows.Forms.Label
    $subtitleLabel.Text = 'Real-time endpoint network monitor'
    $subtitleLabel.Font = Get-RugBusterFont -Role Mono -Size 9
    $subtitleLabel.ForeColor = $Script:Brand.TextMuted
    $subtitleLabel.AutoSize = $true
    $subtitleLabel.Location = New-Object System.Drawing.Point(80, 38)
    $header.Controls.Add($subtitleLabel)

    $statusLabel = New-Object System.Windows.Forms.Label
    $statusLabel.Text = '* AKTIVNO'
    $statusLabel.Font = Get-RugBusterFont -Role Mono -Size 10 -Style Bold
    $statusLabel.ForeColor = $Script:Brand.NeonGreen
    $statusLabel.AutoSize = $true
    $statusLabel.Anchor = 'Top,Right'
    $statusLabel.Location = New-Object System.Drawing.Point(($header.Width - 120), 26)
    $header.Controls.Add($statusLabel)
    $Script:StatusLabel = $statusLabel

    # ---- Tabs ----
    $tabs = New-Object System.Windows.Forms.TabControl
    $tabs.Dock = 'Fill'
    $tabs.Font = Get-RugBusterFont -Role Body -Size 10
    $form.Controls.Add($tabs)
    $tabs.BringToFront()

    $tabMonitor = New-Object System.Windows.Forms.TabPage
    $tabMonitor.Text = 'Monitor'
    $tabMonitor.BackColor = $Script:Brand.DarkBg
    $tabs.TabPages.Add($tabMonitor)

    $tabHistory = New-Object System.Windows.Forms.TabPage
    $tabHistory.Text = 'Istorija upozorenja'
    $tabHistory.BackColor = $Script:Brand.DarkBg
    $tabs.TabPages.Add($tabHistory)

    # ---- Monitor grid ----
    $dgvMonitor = New-Object System.Windows.Forms.DataGridView
    $dgvMonitor.Dock = 'Fill'
    Set-RugBusterGridStyle -Grid $dgvMonitor
    $dgvMonitor.ColumnCount = 7
    $cols = @('Vreme', 'Proces', 'PID', 'Destinacija', 'Port', 'Potpis', 'Severity')
    for ($i = 0; $i -lt $cols.Count; $i++) { $dgvMonitor.Columns[$i].Name = $cols[$i]; $dgvMonitor.Columns[$i].HeaderText = $cols[$i] }
    $dgvMonitor.Columns['Destinacija'].AutoSizeMode = 'Fill'
    $tabMonitor.Controls.Add($dgvMonitor)
    $Script:MonitorGrid = $dgvMonitor

    # ---- History grid ----
    $dgvHistory = New-Object System.Windows.Forms.DataGridView
    $dgvHistory.Dock = 'Fill'
    Set-RugBusterGridStyle -Grid $dgvHistory
    $dgvHistory.Columns.Add('Id', 'Id') | Out-Null
    $dgvHistory.Columns['Id'].Visible = $false
    foreach ($name in @('Vreme', 'Proces', 'Destinacija', 'Severity', 'Razlog', 'Status')) {
        $dgvHistory.Columns.Add($name, $name) | Out-Null
    }
    $dgvHistory.Columns['Destinacija'].AutoSizeMode = 'Fill'

    $btnFalse = New-Object System.Windows.Forms.DataGridViewButtonColumn
    $btnFalse.Name = 'LaznaUzbuna'
    $btnFalse.HeaderText = ''
    $btnFalse.Text = 'Lazna uzbuna'
    $btnFalse.UseColumnTextForButtonValue = $true
    $dgvHistory.Columns.Add($btnFalse) | Out-Null

    $btnOk = New-Object System.Windows.Forms.DataGridViewButtonColumn
    $btnOk.Name = 'IstrazenoOk'
    $btnOk.HeaderText = ''
    $btnOk.Text = 'Istrazeno - OK'
    $btnOk.UseColumnTextForButtonValue = $true
    $dgvHistory.Columns.Add($btnOk) | Out-Null

    $dgvHistory.Add_CellClick({
        param($sender, $e)
        if ($e.RowIndex -lt 0) { return }
        $row = $Script:HistoryGrid.Rows[$e.RowIndex]
        $alertId = $row.Cells['Id'].Value
        switch ($Script:HistoryGrid.Columns[$e.ColumnIndex].Name) {
            'LaznaUzbuna' { Set-RugBusterAlertStatus -AlertId $alertId -Status 'Lazna uzbuna' }
            'IstrazenoOk' { Set-RugBusterAlertStatus -AlertId $alertId -Status 'Istrazeno - OK' }
        }
    })

    $tabHistory.Controls.Add($dgvHistory)
    $Script:HistoryGrid = $dgvHistory
    $Script:Tabs = $tabs
    $Script:TabHistory = $tabHistory

    # ---- Tray icon ----
    $notifyIcon = New-Object System.Windows.Forms.NotifyIcon
    $notifyIcon.Text = 'RugBuster Shield'
    if (Test-Path $Script:Config.IconIcoPath) {
        $notifyIcon.Icon = New-Object System.Drawing.Icon($Script:Config.IconIcoPath)
    }
    $notifyIcon.Visible = $true

    $trayMenu = New-Object System.Windows.Forms.ContextMenuStrip
    $openItem = $trayMenu.Items.Add('Otvori')
    $openItem.Add_Click({ Show-RugBusterMainWindow })
    $exitItem = $trayMenu.Items.Add('Izlaz')
    $exitItem.Add_Click({ $Script:ExitRequested = $true; $form.Close() })
    $notifyIcon.ContextMenuStrip = $trayMenu
    $notifyIcon.Add_DoubleClick({ Show-RugBusterMainWindow })
    $Script:NotifyIcon = $notifyIcon

    $form.Add_FormClosing({
        param($sender, $e)
        if (-not $Script:ExitRequested) {
            $e.Cancel = $true
            $form.Hide()
        } else {
            $Script:NotifyIcon.Visible = $false
        }
    })

    return $form
}

function Set-RugBusterGridStyle {
    param([System.Windows.Forms.DataGridView]$Grid)
    $Grid.BackgroundColor = $Script:Brand.DarkBg
    $Grid.GridColor = $Script:Brand.DarkCard
    $Grid.BorderStyle = 'None'
    $Grid.RowHeadersVisible = $false
    $Grid.AllowUserToAddRows = $false
    $Grid.AllowUserToDeleteRows = $false
    $Grid.ReadOnly = $true
    $Grid.SelectionMode = 'FullRowSelect'
    $Grid.AutoSizeColumnsMode = 'AllCells'
    $Grid.Font = Get-RugBusterFont -Role Mono -Size 9
    $Grid.DefaultCellStyle.BackColor = $Script:Brand.DarkCard
    $Grid.DefaultCellStyle.ForeColor = $Script:Brand.TextPrimary
    $Grid.DefaultCellStyle.SelectionBackColor = $Script:Brand.NeonCyan
    $Grid.DefaultCellStyle.SelectionForeColor = $Script:Brand.DarkBg
    $Grid.ColumnHeadersDefaultCellStyle.BackColor = $Script:Brand.DarkPanel
    $Grid.ColumnHeadersDefaultCellStyle.ForeColor = $Script:Brand.NeonCyan
    $Grid.EnableHeadersVisualStyles = $false
}

function Add-RugBusterMonitorRow {
    param($Time, $ProcessName, $ProcessId, $Destination, $Port, $SignatureStatus, $Severity)
    if (-not $Script:MonitorGrid) { return }
    $rowIndex = $Script:MonitorGrid.Rows.Add($Time.ToString('HH:mm:ss'), $ProcessName, $ProcessId, $Destination, $Port, $SignatureStatus, $Severity)
    $row = $Script:MonitorGrid.Rows[$rowIndex]
    $row.DefaultCellStyle.ForeColor = Get-RugBusterSeverityColor -Level $Severity
    # Keep the grid bounded so long-running sessions don't grow memory unbounded.
    while ($Script:MonitorGrid.Rows.Count -gt 500) { $Script:MonitorGrid.Rows.RemoveAt(0) }
}

function Get-RugBusterSeverityColor {
    param([string]$Level)
    switch ($Level) {
        'LOW'    { return $Script:Brand.NeonGreen }
        'MEDIUM' { return $Script:Brand.NeonOrange }
        'HIGH'   { return $Script:Brand.DangerRed }
        default  { return $Script:Brand.TextPrimary }
    }
}

function Update-RugBusterHistoryGrid {
    if (-not $Script:HistoryGrid) { return }
    $Script:HistoryGrid.Rows.Clear()
    foreach ($entry in ($Script:AlertHistory | Sort-Object Timestamp -Descending)) {
        $t = [datetime]::Parse($entry.Timestamp).ToString('yyyy-MM-dd HH:mm:ss')
        $rowIndex = $Script:HistoryGrid.Rows.Add($entry.Id, $t, $entry.ProcessName, $entry.Destination, $entry.Severity, $entry.Reasons, $entry.Status, '', '')
        $row = $Script:HistoryGrid.Rows[$rowIndex]
        $row.DefaultCellStyle.ForeColor = Get-RugBusterSeverityColor -Level $entry.Severity
    }
}

function Show-RugBusterMainWindow {
    $Script:MainForm.Show()
    $Script:MainForm.WindowState = 'Normal'
    $Script:MainForm.Activate()
}

function Focus-RugBusterAlert {
    param([string]$AlertId)
    Show-RugBusterMainWindow
    $Script:Tabs.SelectedTab = $Script:TabHistory
    foreach ($row in $Script:HistoryGrid.Rows) {
        if ($row.Cells['Id'].Value -eq $AlertId) {
            $Script:HistoryGrid.ClearSelection()
            $row.Selected = $true
            $Script:HistoryGrid.FirstDisplayedScrollingRowIndex = $row.Index
            break
        }
    }
}
#endregion

#region ---------------------------------------------------------------- Inter-process focus signal
function Watch-RugBusterFocusSignal {
    $watcher = New-Object System.IO.FileSystemWatcher
    $watcher.Path = $Script:Config.ScriptDir
    $watcher.Filter = [System.IO.Path]::GetFileName($Script:Config.SignalPath)
    $watcher.EnableRaisingEvents = $true

    $action = {
        try {
            Start-Sleep -Milliseconds 100  # let the writer finish
            $alertId = Get-Content -Path $Event.MessageData.SignalPath -Raw -ErrorAction SilentlyContinue
            if ($alertId) {
                $Event.MessageData.Form.Invoke([Action] { Focus-RugBusterAlert -AlertId $alertId.Trim() })
            }
            Remove-Item -Path $Event.MessageData.SignalPath -Force -ErrorAction SilentlyContinue
        } catch {
            Write-Warning "RugBuster: failed to process focus signal ($($_.Exception.Message))"
        }
    }
    $messageData = [pscustomobject]@{ SignalPath = $Script:Config.SignalPath; Form = $Script:MainForm }
    Register-ObjectEvent -InputObject $watcher -EventName Created -Action $action -MessageData $messageData | Out-Null
    Register-ObjectEvent -InputObject $watcher -EventName Changed -Action $action -MessageData $messageData | Out-Null
    $Script:FocusWatcher = $watcher
}
#endregion

#region ---------------------------------------------------------------- Entry point
$Script:ExitRequested = $false
$Script:Mutex = New-Object System.Threading.Mutex($false, 'Global\RugBusterShieldGUI_Mutex')

if (-not $Script:Mutex.WaitOne(0)) {
    # Another instance owns the mutex. If this launch came from a toast's
    # "Detalji" button (rugbuster:<id> -> -FocusAlertId), hand the alert
    # id to the running instance via a signal file and exit quietly.
    if ($FocusAlertId) {
        Set-Content -Path $Script:Config.SignalPath -Value $FocusAlertId -Encoding UTF8
    }
    return
}

New-RugBusterIcon
Initialize-RugBusterWhitelist
$Script:WhitelistSet = Get-RugBusterWhitelist
$Script:State = Import-RugBusterState
$Script:AlertHistory = Import-RugBusterHistory
Initialize-RugBusterToast
Register-RugBusterProtocolHandler

$Script:MainForm = New-RugBusterMainForm
Update-RugBusterHistoryGrid
Watch-RugBusterFocusSignal

$Script:ScanTimer = New-Object System.Windows.Forms.Timer
$Script:ScanTimer.Interval = $Script:Config.IntervalMsFast
$Script:ScanTimer.Add_Tick({ Invoke-RugBusterScan })
$Script:ScanTimer.Start()

if ($FocusAlertId) {
    $Script:MainForm.Add_Shown({ Focus-RugBusterAlert -AlertId $FocusAlertId })
} elseif ($MinimizedStart) {
    $Script:MainForm.WindowState = 'Minimized'
    $Script:MainForm.Add_Shown({ $Script:MainForm.Hide() })
}

[System.Windows.Forms.Application]::Run($Script:MainForm)

$Script:Mutex.ReleaseMutex()
#endregion
