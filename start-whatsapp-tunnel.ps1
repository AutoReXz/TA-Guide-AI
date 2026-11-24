[CmdletBinding()]
param(
    [ValidatePattern('^[A-Za-z0-9_-]+$')]
    [string]$WorkflowId = 'hgMqI5D4suf6B0Ii',
    [ValidatePattern('^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$')]
    [string]$WebhookId = '2d33c8df-f64e-46c7-bac9-9c36f7c7b7ae',
    [string]$CloudflaredPath = 'C:\Program Files (x86)\cloudflared\cloudflared.exe',
    [ValidateRange(30, 600)]
    [int]$StartupTimeoutSeconds = 120
)

$ErrorActionPreference = 'Stop'

function Invoke-NativeCommand {
    param(
        [Parameter(Mandatory = $true)]
        [string]$FilePath,
        [string[]]$Arguments
    )

    $previousErrorActionPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $output = & $FilePath @Arguments 2>&1
        $exitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }

    [pscustomobject]@{
        ExitCode = $exitCode
        Output   = @($output)
    }
}

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$cloudflaredCommand = $null
if (-not (Test-Path -LiteralPath $CloudflaredPath -PathType Leaf)) {
    $cloudflaredCommand = Get-Command cloudflared.exe -ErrorAction Stop
    $CloudflaredPath = $cloudflaredCommand.Source
}
$runId = [guid]::NewGuid().ToString('N')
$stdoutPath = Join-Path $env:TEMP "cloudflared-$runId.out.log"
$stderrPath = Join-Path $env:TEMP "cloudflared-$runId.err.log"
$cloudflaredProcess = $null

Push-Location $projectRoot
try {
    New-Item -ItemType File -Path $stdoutPath, $stderrPath -Force | Out-Null
    $cloudflaredProcess = Start-Process `
        -FilePath $CloudflaredPath `
        -ArgumentList @('tunnel', '--no-autoupdate', '--url', 'http://localhost:5678') `
        -RedirectStandardOutput $stdoutPath `
        -RedirectStandardError $stderrPath `
        -PassThru `
        -NoNewWindow

    $urlPattern = '(?i)https://[a-z0-9-]+\.trycloudflare\.com'
    $publicUrl = $null
    $deadline = (Get-Date).AddSeconds($StartupTimeoutSeconds)

    while (-not $publicUrl -and (Get-Date) -lt $deadline) {
        if ($cloudflaredProcess.HasExited) {
            $cloudflaredOutput = (Get-Content -LiteralPath $stdoutPath -Raw -ErrorAction SilentlyContinue) +
                (Get-Content -LiteralPath $stderrPath -Raw -ErrorAction SilentlyContinue)
            throw "cloudflared berhenti dengan exit code $($cloudflaredProcess.ExitCode).`n$cloudflaredOutput"
        }

        $cloudflaredOutput = (Get-Content -LiteralPath $stdoutPath -Raw -ErrorAction SilentlyContinue) +
            (Get-Content -LiteralPath $stderrPath -Raw -ErrorAction SilentlyContinue)
        if ($cloudflaredOutput -match $urlPattern) {
            $publicUrl = $matches[0].TrimEnd('/')
            break
        }

        Start-Sleep -Seconds 1
    }

    if (-not $publicUrl) {
        throw "URL Quick Tunnel tidak muncul dalam $StartupTimeoutSeconds detik. Periksa koneksi cloudflared."
    }

    $tunnelHost = ([uri]$publicUrl).Host
    $env:WHATSAPP_TUNNEL_HOST = $tunnelHost
    $env:WHATSAPP_WEBHOOK_ID = $WebhookId
    $env:N8N_WEBHOOK_URL = "$publicUrl/"

    Write-Host "Quick Tunnel: $publicUrl"
    Write-Host 'Memperbarui proxy dan n8n...'
    $composeUp = Invoke-NativeCommand -FilePath 'docker.exe' -Arguments @('compose', 'up', '-d', '--no-deps', 'webhook-proxy', 'n8n')
    $composeUp.Output | ForEach-Object { Write-Host $_ }
    if ($composeUp.ExitCode -ne 0) {
        throw 'Gagal memperbarui container webhook-proxy/n8n.'
    }

    $caddyValidation = Invoke-NativeCommand -FilePath 'docker.exe' -Arguments @('compose', 'exec', '-T', 'webhook-proxy', 'caddy', 'validate', '--config', '/etc/caddy/Caddyfile', '--adapter', 'caddyfile')
    if ($caddyValidation.ExitCode -ne 0) {
        throw "Validasi konfigurasi Caddy gagal:`n$($caddyValidation.Output -join "`n")"
    }

    $n8nReady = $false
    $deadline = (Get-Date).AddSeconds($StartupTimeoutSeconds)
    while (-not $n8nReady -and (Get-Date) -lt $deadline) {
        $healthCheck = Invoke-NativeCommand -FilePath 'curl.exe' -Arguments @('-sS', '-o', 'NUL', '-w', '%{http_code}', 'http://localhost:5678/healthz')
        $healthStatus = ($healthCheck.Output -join '').Trim()
        $n8nReady = $healthCheck.ExitCode -eq 0 -and $healthStatus -eq '200'
        if (-not $n8nReady) {
            Start-Sleep -Seconds 2
        }
    }
    if (-not $n8nReady) {
        throw 'n8n belum siap setelah batas waktu; lihat `docker compose logs n8n`.'
    }

    $tunnelReady = $false
    $deadline = (Get-Date).AddSeconds($StartupTimeoutSeconds)
    while (-not $tunnelReady -and (Get-Date) -lt $deadline) {
        $statusResponse = Invoke-NativeCommand -FilePath 'curl.exe' -Arguments @('-sS', '-o', 'NUL', '-w', '%{http_code}', "$publicUrl/")
        $rootStatus = ($statusResponse.Output -join '').Trim()
        $statusRequestSucceeded = $statusResponse.ExitCode -eq 0
        $bodyResponse = Invoke-NativeCommand -FilePath 'curl.exe' -Arguments @('-sS', "$publicUrl/")
        $rootBody = $bodyResponse.Output -join "`n"
        $bodyRequestSucceeded = $bodyResponse.ExitCode -eq 0
        $tunnelReady = $statusRequestSucceeded -and $bodyRequestSucceeded -and $rootStatus -eq '404' -and $rootBody.Trim() -eq 'Not Found'
        if (-not $tunnelReady) {
            Start-Sleep -Seconds 2
        }
    }
    if (-not $tunnelReady) {
        throw 'Tunnel belum mencapai Caddy. Root publik harus menghasilkan 404 dari Caddy.'
    }

    Write-Host 'Mendaftarkan ulang callback WhatsApp di Meta...'
    $publish = Invoke-NativeCommand -FilePath 'docker.exe' -Arguments @('compose', 'exec', '-T', 'n8n', 'n8n', 'publish:workflow', "--id=$WorkflowId")
    $publish.Output | ForEach-Object { Write-Host $_ }
    if ($publish.ExitCode -ne 0) {
        throw "Publish ulang workflow gagal; periksa credential dan log n8n.`n$($publish.Output -join "`n")"
    }

    $callbackReady = $false
    $callbackUrl = "$publicUrl/webhook/$WebhookId/webhook?hub.mode=subscribe&hub.verify_token=launcher-check&hub.challenge=12345"
    $deadline = (Get-Date).AddSeconds($StartupTimeoutSeconds)
    while (-not $callbackReady -and (Get-Date) -lt $deadline) {
        $callbackResponse = Invoke-NativeCommand -FilePath 'curl.exe' -Arguments @('-sS', '-o', 'NUL', '-w', '%{http_code}', $callbackUrl)
        $callbackStatus = ($callbackResponse.Output -join '').Trim()
        $callbackReady = $callbackResponse.ExitCode -eq 0 -and $callbackStatus -eq '200'
        if (-not $callbackReady) { Start-Sleep -Seconds 2 }
    }
    if (-not $callbackReady) {
        throw 'Callback production belum menjawab HTTP 200; periksa status workflow dan log n8n.'
    }

    Write-Host "Callback production: $publicUrl/webhook/$WebhookId/webhook"
    Write-Host "Tunnel aktif (PID $($cloudflaredProcess.Id)). Biarkan jendela ini berjalan; tekan Ctrl+C untuk menghentikannya."

    while (-not $cloudflaredProcess.HasExited) {
        Start-Sleep -Seconds 2
    }

    throw "cloudflared berhenti dengan exit code $($cloudflaredProcess.ExitCode)."
}
finally {
    if ($cloudflaredProcess -and -not $cloudflaredProcess.HasExited) {
        Stop-Process -Id $cloudflaredProcess.Id -ErrorAction SilentlyContinue
        Wait-Process -Id $cloudflaredProcess.Id -Timeout 10 -ErrorAction SilentlyContinue
    }
    Remove-Item -LiteralPath $stdoutPath, $stderrPath -Force -ErrorAction SilentlyContinue
    Pop-Location
}
