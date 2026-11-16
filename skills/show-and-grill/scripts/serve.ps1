<#
.SYNOPSIS
Serve one grill work folder on 127.0.0.1 (for Windows without Python).

.DESCRIPTION
A small static file server with the same guards as serve.py:

- Listens only on 127.0.0.1 (the loopback address).
- Answers 403 unless the Host header is 127.0.0.1:<port> or localhost:<port>
  (any letter case). This blocks DNS rebinding.
- Answers 404 for any path part that starts with "." after percent-decoding
  (hidden files such as .serve.json, and "..").
- Answers 404 for paths outside the folder, for paths through a symbolic link
  or junction (reparse point), and for folders (no listing).
- Answers 405 for methods other than GET and HEAD.

It runs in the foreground until Ctrl+C or until its terminal or session ends.
It has no state file, no take-over, no --status, and no idle stop.

When it listens, it prints one line: SERVING http://127.0.0.1:<port>/

.EXAMPLE
powershell -NoProfile -ExecutionPolicy Bypass -File serve.ps1 C:\work\grill
#>
param(
    [Parameter(Mandatory = $true, Position = 0)]
    [string]$Root,

    [ValidateRange(0, 65535)]
    [int]$Port = 0
)

Set-StrictMode -Version 2
$ErrorActionPreference = 'Stop'

$MaxHeadBytes = 8192
$ReadTimeoutMs = 5000
$IsWindowsPath = ([System.IO.Path]::DirectorySeparatorChar -eq '\')

$ContentTypes = @{
    '.html' = 'text/html; charset=utf-8'
    '.htm'  = 'text/html; charset=utf-8'
    '.css'  = 'text/css; charset=utf-8'
    '.js'   = 'text/javascript; charset=utf-8'
    '.mjs'  = 'text/javascript; charset=utf-8'
    '.json' = 'application/json'
    '.md'   = 'text/markdown; charset=utf-8'
    '.txt'  = 'text/plain; charset=utf-8'
    '.svg'  = 'image/svg+xml'
    '.png'  = 'image/png'
    '.jpg'  = 'image/jpeg'
    '.jpeg' = 'image/jpeg'
    '.gif'  = 'image/gif'
    '.webp' = 'image/webp'
    '.ico'  = 'image/x-icon'
    '.woff2' = 'font/woff2'
}

$Reasons = @{
    200 = 'OK'
    400 = 'Bad Request'
    403 = 'Forbidden'
    404 = 'Not Found'
    405 = 'Method Not Allowed'
    431 = 'Request Header Fields Too Large'
}

function Test-HostAllowed {
    <# True only for 127.0.0.1:<port> or localhost:<port>, any letter case. #>
    param([AllowNull()][string]$HostHeader, [int]$ServerPort)
    if ([string]::IsNullOrEmpty($HostHeader)) { return $false }
    $value = $HostHeader.Trim().ToLowerInvariant()
    return ($value -ceq "127.0.0.1:$ServerPort") -or ($value -ceq "localhost:$ServerPort")
}

function Get-PathParts {
    <#
    Percent-decode the path of a request target and split it into parts.
    Returns $null when the target must not be served: not starting with "/",
    a part that starts with "." (hidden files and ".."), or a part with ":"
    (drive names and alternate data streams).
    #>
    param([string]$Target)
    if (-not $Target.StartsWith('/')) { return $null }
    $path = $Target
    $cut = $path.IndexOfAny([char[]]@('?', '#'))
    if ($cut -ge 0) { $path = $path.Substring(0, $cut) }
    $decoded = [System.Uri]::UnescapeDataString($path).Replace('\', '/')
    $parts = New-Object System.Collections.Generic.List[string]
    foreach ($part in $decoded.Split('/')) {
        if ($part.Length -eq 0) { continue }
        if ($part.StartsWith('.')) { return $null }
        if ($part.Contains(':')) { return $null }
        if ($part.IndexOf([char]0) -ge 0) { return $null }
        $parts.Add($part)
    }
    return , $parts.ToArray()
}

function Resolve-ServedFile {
    <#
    Return the full path of a regular file inside $RootFull for the parts,
    or $null (outside the folder, a reparse point on the way, a folder, or
    missing).
    #>
    param([string]$RootFull, [string[]]$Parts)
    if ($Parts.Count -eq 0) { return $null }
    $sep = [System.IO.Path]::DirectorySeparatorChar
    $full = [System.IO.Path]::GetFullPath($RootFull + $sep + ($Parts -join $sep))
    $comparison = [System.StringComparison]::Ordinal
    if ($IsWindowsPath) { $comparison = [System.StringComparison]::OrdinalIgnoreCase }
    if (-not $full.StartsWith($RootFull + $sep, $comparison)) { return $null }
    $current = $RootFull
    foreach ($part in $full.Substring($RootFull.Length + 1).Split($sep)) {
        $current = $current + $sep + $part
        if (-not [System.IO.File]::Exists($current) -and -not [System.IO.Directory]::Exists($current)) {
            return $null
        }
        $attributes = [System.IO.File]::GetAttributes($current)
        if (($attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) { return $null }
    }
    if ([System.IO.Directory]::Exists($full)) { return $null }
    if (-not [System.IO.File]::Exists($full)) { return $null }
    return $full
}

function Read-RequestHead {
    <# Read bytes up to the blank line after the headers. Returns the text, or an int status on error. #>
    param([System.IO.Stream]$Stream)
    $buffer = New-Object byte[] 1024
    $head = New-Object System.IO.MemoryStream
    $latin1 = [System.Text.Encoding]::GetEncoding(28591)
    while ($true) {
        $count = $Stream.Read($buffer, 0, $buffer.Length)
        if ($count -le 0) { return 400 }
        $head.Write($buffer, 0, $count)
        $text = $latin1.GetString($head.ToArray())
        $end = $text.IndexOf("`r`n`r`n")
        if ($end -ge 0) { return $text.Substring(0, $end) }
        if ($head.Length -ge $MaxHeadBytes) { return 431 }
    }
}

function Send-Response {
    param(
        [System.IO.Stream]$Stream,
        [int]$Status,
        [string]$ContentType = 'text/plain; charset=utf-8',
        [byte[]]$Body = $null,
        [bool]$HeadOnly = $false,
        [string[]]$ExtraHeaders = @()
    )
    if ($null -eq $Body) {
        $Body = [System.Text.Encoding]::UTF8.GetBytes("$Status $($Reasons[$Status])`n")
    }
    $lines = New-Object System.Collections.Generic.List[string]
    $lines.Add("HTTP/1.1 $Status $($Reasons[$Status])")
    $lines.Add("Content-Type: $ContentType")
    $lines.Add("Content-Length: $($Body.Length)")
    $lines.Add('Cache-Control: no-store')
    $lines.Add('X-Content-Type-Options: nosniff')
    $lines.Add('Connection: close')
    foreach ($header in $ExtraHeaders) { $lines.Add($header) }
    $headBytes = [System.Text.Encoding]::ASCII.GetBytes(($lines -join "`r`n") + "`r`n`r`n")
    $Stream.Write($headBytes, 0, $headBytes.Length)
    if (-not $HeadOnly -and $Body.Length -gt 0) { $Stream.Write($Body, 0, $Body.Length) }
    $Stream.Flush()
}

function Invoke-Request {
    param([System.IO.Stream]$Stream, [string]$RootFull, [int]$ServerPort)
    $head = Read-RequestHead -Stream $Stream
    if ($head -is [int]) { Send-Response -Stream $Stream -Status $head; return }
    $lines = $head.Split(@("`r`n"), [System.StringSplitOptions]::None)
    $requestLine = $lines[0].Split(' ')
    if ($requestLine.Count -ne 3 -or -not $requestLine[2].StartsWith('HTTP/')) {
        Send-Response -Stream $Stream -Status 400
        return
    }
    $method = $requestLine[0]
    $target = $requestLine[1]
    $hostHeader = $null
    for ($i = 1; $i -lt $lines.Count; $i++) {
        $colon = $lines[$i].IndexOf(':')
        if ($colon -le 0) { continue }
        if ($lines[$i].Substring(0, $colon).Trim() -ieq 'host') {
            if ($null -ne $hostHeader) { Send-Response -Stream $Stream -Status 400; return }
            $hostHeader = $lines[$i].Substring($colon + 1).Trim()
        }
    }
    if ($method -cne 'GET' -and $method -cne 'HEAD') {
        Send-Response -Stream $Stream -Status 405 -ExtraHeaders @('Allow: GET, HEAD')
        return
    }
    $headOnly = ($method -ceq 'HEAD')
    if (-not (Test-HostAllowed -HostHeader $hostHeader -ServerPort $ServerPort)) {
        Send-Response -Stream $Stream -Status 403 -HeadOnly $headOnly
        return
    }
    $file = $null
    $parts = Get-PathParts -Target $target
    if ($null -ne $parts) {
        try { $file = Resolve-ServedFile -RootFull $RootFull -Parts $parts } catch { $file = $null }
    }
    if ($null -eq $file) {
        Send-Response -Stream $Stream -Status 404 -HeadOnly $headOnly
        return
    }
    $extension = [System.IO.Path]::GetExtension($file).ToLowerInvariant()
    $type = 'application/octet-stream'
    if ($ContentTypes.ContainsKey($extension)) { $type = $ContentTypes[$extension] }
    $body = [System.IO.File]::ReadAllBytes($file)
    Send-Response -Stream $Stream -Status 200 -ContentType $type -Body $body -HeadOnly $headOnly
}

if (-not [System.IO.Directory]::Exists($Root)) {
    [Console]::Error.WriteLine("NOT_A_DIRECTORY $Root")
    exit 1
}
$sepChars = [char[]]@([System.IO.Path]::DirectorySeparatorChar, [System.IO.Path]::AltDirectorySeparatorChar)
$RootFull = [System.IO.Path]::GetFullPath((Resolve-Path -LiteralPath $Root).ProviderPath).TrimEnd($sepChars)

$listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, $Port)
try {
    $listener.Start()
} catch {
    [Console]::Error.WriteLine("CANNOT_LISTEN 127.0.0.1:$Port $($_.Exception.Message)")
    exit 1
}
$actualPort = ([System.Net.IPEndPoint]$listener.LocalEndpoint).Port
[Console]::Out.WriteLine("SERVING http://127.0.0.1:$actualPort/")
[Console]::Out.Flush()

try {
    while ($true) {
        if (-not $listener.Pending()) {
            Start-Sleep -Milliseconds 20
            continue
        }
        $client = $listener.AcceptTcpClient()
        try {
            $client.ReceiveTimeout = $ReadTimeoutMs
            $client.SendTimeout = $ReadTimeoutMs
            $stream = $client.GetStream()
            $stream.ReadTimeout = $ReadTimeoutMs
            $stream.WriteTimeout = $ReadTimeoutMs
            Invoke-Request -Stream $stream -RootFull $RootFull -ServerPort $actualPort
        } catch {
            # One broken request (bad request line, closed connection, read
            # timeout) closes only that connection. The server keeps running.
        } finally {
            $client.Close()
        }
    }
} finally {
    $listener.Stop()
}
