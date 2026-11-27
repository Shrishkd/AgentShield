# Save once, load into the current PowerShell environment, or copy for the explorer.
[CmdletBinding()]
param(
    [ValidateSet('Save', 'Load', 'Copy')]
    [string]$Action = 'Load',
    [ValidateRange(1, 65535)]
    [int]$Port = 8766
)

$ErrorActionPreference = 'Stop'
if ($env:OS -ne 'Windows_NT') {
    throw 'This helper uses Windows user-account encryption. Run it in Windows PowerShell.'
}
$folder = Join-Path $env:LOCALAPPDATA 'AgentShield\keys'
$path = Join-Path $folder "loopback-$Port-hybrid-rag.dpapi"

if ($Action -eq 'Save') {
    $secret = Read-Host "Paste the existing hybrid-rag application key for port $Port (hidden)" -AsSecureString
    try {
        if ($secret.Length -eq 0) { throw 'The application key is empty.' }
        $encrypted = ConvertFrom-SecureString -SecureString $secret
        [void][IO.Directory]::CreateDirectory($folder)
        $temporary = Join-Path $folder ([Guid]::NewGuid().ToString('N'))
        try {
            [IO.File]::WriteAllText($temporary, $encrypted)
            if ([IO.File]::Exists($path)) {
                # Replace atomically, even if the old credential was unreadable.
                $backup = "$temporary.bak"
                [IO.File]::Replace($temporary, $path, $backup)
                [IO.File]::Delete($backup)
            } else {
                [IO.File]::Move($temporary, $path)
            }
        } finally {
            [IO.File]::Delete($temporary)
        }
    } finally {
        $secret.Dispose()
    }
    Write-Host "Saved the hybrid-rag key for port $Port under your Windows account."
    return
}

if (-not [IO.File]::Exists($path)) {
    throw "No saved hybrid-rag key for port $Port. Run .\scripts\rag-key.ps1 Save -Port $Port first."
}
$secret = ConvertTo-SecureString -String ([IO.File]::ReadAllText($path))
try {
    $credential = [PSCredential]::new('hybrid-rag', $secret)
    if ($Action -eq 'Copy') {
        Set-Clipboard -Value $credential.GetNetworkCredential().Password
        Write-Host 'Copied the key. Paste it in the Application field in the explorer.'
    } else {
        $env:AGENTSHIELD_APP_KEY = $credential.GetNetworkCredential().Password
        $env:AGENTSHIELD_APP_ID = 'hybrid-rag'
        $env:AGENTSHIELD_URL = "http://127.0.0.1:$Port"
        Write-Host "Loaded the hybrid-rag credential for $env:AGENTSHIELD_URL in this terminal."
    }
} finally {
    $secret.Dispose()
}
