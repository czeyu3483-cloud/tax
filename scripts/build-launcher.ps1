$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path $PSScriptRoot -Parent
$compilerPath = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
$outputPath = Join-Path $repoRoot 'XiaochiLauncher.exe'
$sourcePath = Join-Path $PSScriptRoot 'windows-launcher.cs'
& $compilerPath /nologo /target:winexe /platform:x64 /codepage:65001 /reference:System.Windows.Forms.dll /reference:System.Drawing.dll "/out:$outputPath" $sourcePath
if ($LASTEXITCODE -ne 0) { throw 'Launcher compilation failed.' }
