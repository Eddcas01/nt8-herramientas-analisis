# Verificador de compilacion de NinjaScript: usa el MISMO Roslyn y las MISMAS referencias que NinjaTrader 8, asi que
# detecta los errores de compilacion ANTES de copiar el archivo a NT. No emite nada ni toca NT.
#
# Uso:   powershell -NoProfile -ExecutionPolicy Bypass -File herramientas\nt8check\nt8check.ps1 archivo1.cs [archivo2.cs ...] [-w]
#        (-w muestra tambien los avisos; los CS1701 de versiones de ensamblado son ruido normal de NT)
# Salida: "== archivo.cs: N errores, M avisos"; codigo de salida 1 si hay algun error.
# Requiere NinjaTrader 8 instalado en C:\Program Files\NinjaTrader 8 y haber compilado alguna vez en NT (usa
# Documents\NinjaTrader 8\bin\Custom\NinjaTrader.Custom.dll para los indicadores y la clase Strategy).
$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$nt   = 'C:\Program Files\NinjaTrader 8\bin'
$fw   = 'C:\Windows\Microsoft.NET\Framework64\v4.0.30319'
$out  = Join-Path $env:TEMP 'nt8check'
$exe  = Join-Path $out 'nt8check.exe'
$src  = Join-Path $here 'Nt8Check.cs'

# Se compila una sola vez (o cuando cambia Nt8Check.cs) con el csc del Framework (C# 5); el Roslyn de NT hace el trabajo real.
if (-not (Test-Path $exe) -or (Get-Item $src).LastWriteTime -gt (Get-Item $exe).LastWriteTime) {
    New-Item -ItemType Directory -Force $out | Out-Null
    & "$fw\csc.exe" /nologo /optimize /target:exe /nowarn:1701 /out:$exe `
        /r:"$nt\Microsoft.CodeAnalysis.dll" /r:"$nt\Microsoft.CodeAnalysis.CSharp.dll" `
        /r:"$nt\System.Collections.Immutable.dll" /r:"$nt\System.Reflection.Metadata.dll" `
        /r:"$fw\netstandard.dll" /r:"$fw\System.Runtime.dll" /r:System.Core.dll $src
    if ($LASTEXITCODE -ne 0) { throw 'No se pudo compilar Nt8Check.cs' }
}
& $exe @args
exit $LASTEXITCODE
