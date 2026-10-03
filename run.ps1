# MIDI-only playback through Ableton Live on Windows.
param(
    [string]$MidiBus = 'Twelve Tones',
    [switch]$Visualiser,
    [switch]$Check,
    [switch]$ExportVideo,
    [switch]$StopExport,
    [string]$AudioDevice,
    [double]$Duration = 0
)
$ErrorActionPreference = 'Stop'
if (($ExportVideo -or $StopExport) -and ($Visualiser -or $Check)) {
    throw '-ExportVideo cannot be combined with -Visualiser or -Check.'
}
$pythonPath = Join-Path $PSScriptRoot '.venv-windows/Scripts/python.exe'
if (!(Test-Path -LiteralPath $pythonPath)) {
    throw 'Create .venv-windows and install mido, python-rtmidi, and pygame as described in README.md.'
}
$scriptName = if ($ExportVideo -or $StopExport) { 'export_video.py' } else { 'playback.py' }
$runnerArgs = @((Join-Path $PSScriptRoot $scriptName), '--port', $MidiBus)
if ($StopExport) { $runnerArgs += '--stop' }
if ($AudioDevice) {
    if (!$ExportVideo) { throw '-AudioDevice requires -ExportVideo.' }
    $runnerArgs += @('--audio-device', $AudioDevice)
}
if ($Visualiser) { $runnerArgs += '--visualiser' }
if ($Check) { $runnerArgs += '--check' }
if ($Duration -ne 0) { $runnerArgs += @('--duration', $Duration.ToString([Globalization.CultureInfo]::InvariantCulture)) }
Push-Location $PSScriptRoot
try {
    & $pythonPath @runnerArgs
    if ($LASTEXITCODE -ne 0 -and $LASTEXITCODE -ne 130) { throw "Playback exited with code $LASTEXITCODE" }
}
finally { Pop-Location }
