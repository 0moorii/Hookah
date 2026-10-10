#Requires -Version 5.1
# Shared foreground setup helpers; no administrator privileges are required.
$ErrorActionPreference = "Stop"

function Invoke-HookahChecked {
    param([string]$Executable, [string[]]$Arguments)
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Command failed ($LASTEXITCODE): $Executable" }
}

function Get-HookahPython {
    param([string]$PythonExe)
    if ($PythonExe) {
        return [PSCustomObject]@{ Executable = (Get-Command $PythonExe -ErrorAction Stop).Source; Arguments = @() }
    }
    $launcher = Get-Command "py.exe" -ErrorAction SilentlyContinue
    if ($launcher) { return [PSCustomObject]@{ Executable = $launcher.Source; Arguments = @("-3") } }
    return [PSCustomObject]@{ Executable = (Get-Command "python.exe" -ErrorAction Stop).Source; Arguments = @() }
}

function Initialize-HookahEnvironment {
    param([string]$PythonExe, [string]$EnvironmentDirectory, [string]$Requirements)
    if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
        throw "Run this setup on Windows. See the README for Linux installation."
    }
    $env:PYTHONUTF8 = "1"
    $env:PYTHONDONTWRITEBYTECODE = "1"
    $command = Get-HookahPython $PythonExe
    Invoke-HookahChecked $command.Executable ($command.Arguments + @("-c", "import sys; assert sys.version_info >= (3, 10), 'Python 3.10+ required'"))
    Invoke-HookahChecked $command.Executable ($command.Arguments + @("-m", "venv", $EnvironmentDirectory))
    $python = Join-Path $EnvironmentDirectory "Scripts\python.exe"
    Invoke-HookahChecked $python @("-m", "pip", "install", "-r", $Requirements)
}
