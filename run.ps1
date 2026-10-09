param([string]$Python = "$PSScriptRoot/.venv/Scripts/python.exe")
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if (!(Test-Path -LiteralPath $Python)) { throw 'Install requirements into .venv, or pass -Python with a configured interpreter.' }
& $Python -m uvicorn app.main:app --host 127.0.0.1 --port 8016
