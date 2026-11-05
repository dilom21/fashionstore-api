<#
5BO / Etapa F - Extraccion AUTORIZADA del esquema real (pg_dump --schema-only + catalogo).

NO se ejecuta por defecto. Requiere -Autorizado y una fuente explicita que NO
sea loopback. La contrasena se solicita de forma interactiva y solo vive en la
variable de entorno del proceso hijo.

Ejemplo:
  powershell -ExecutionPolicy Bypass -File database/saas/80_extraccion_esquema_autorizada_5bo.ps1 `
    -Servidor "db.<proyecto>.supabase.co" -Puerto 5432 -BaseDatos "postgres" `
    -Usuario "postgres" `
    -CarpetaSalida "$env:USERPROFILE\Documents\fashionstore-esquema-5bp" -Autorizado
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Servidor,
    [Parameter(Mandatory = $true)][int]$Puerto,
    [Parameter(Mandatory = $true)][string]$BaseDatos,
    [Parameter(Mandatory = $true)][string]$Usuario,
    [Parameter(Mandatory = $true)][string]$CarpetaSalida,
    [string]$PgBin = "C:\Program Files\PostgreSQL\18\bin",
    [switch]$Autorizado
)

$ErrorActionPreference = "Stop"

if (-not $Autorizado) {
    throw "Rechazado: falta -Autorizado. Este script solo lo ejecuta el estudiante con permiso de lectura."
}
if ($Servidor -in @("127.0.0.1", "localhost", "::1")) {
    throw "Rechazado: use el runner de laboratorio (78_laboratorio_operativo_5bo.py) para loopback."
}
if (-not (Test-Path (Join-Path $PgBin "pg_dump.exe"))) {
    throw "No se encontro pg_dump.exe en $PgBin. Ajuste -PgBin."
}

$marca = Get-Date -Format "yyyyMMdd_HHMMSS"
New-Item -ItemType Directory -Force -Path $CarpetaSalida | Out-Null
$rutaDump = Join-Path $CarpetaSalida "esquema_$marca.sql"
$rutaCatalogo = Join-Path $CarpetaSalida "catalogo_$marca.txt"
$rutaResumen = Join-Path $CarpetaSalida "inventario_$marca.json"

Write-Host "Origen: $Servidor`:$Puerto/$BaseDatos (usuario $Usuario)"
Write-Host "Salida NO versionada: $CarpetaSalida"
$seguro = Read-Host -AsSecureString "Contrasena del rol de lectura"
$bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($seguro)
$texto = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
[Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)

$env:PGPASSWORD = $texto
$env:PGOPTIONS = "-c statement_timeout=120000"
try {
    & (Join-Path $PgBin "pg_dump.exe") `
        --schema-only --no-publications --no-subscriptions `
        --host $Servidor --port $Puerto --username $Usuario --dbname $BaseDatos `
        --file $rutaDump
    if ($LASTEXITCODE -ne 0) { throw "pg_dump fallo con codigo $LASTEXITCODE" }

    & (Join-Path $PgBin "psql.exe") `
        --host $Servidor --port $Puerto --username $Usuario --dbname $BaseDatos `
        --no-psqlrc --tuples-only --no-align `
        --file "database/saas/81_metadatos_catalogo_solo_lectura_5bo.sql" `
        | Out-File -Encoding utf8 $rutaCatalogo
    if ($LASTEXITCODE -ne 0) { throw "psql fallo con codigo $LASTEXITCODE" }
}
finally {
    Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue
    Remove-Item Env:PGOPTIONS -ErrorAction SilentlyContinue
    $texto = $null
}

$dump = Get-Item $rutaDump
$catalogo = Get-Item $rutaCatalogo
[ordered]@{
    fecha = (Get-Date).ToUniversalTime().ToString("o")
    origen = "$Servidor`:$Puerto/$BaseDatos"
    esquema_bytes = $dump.Length
    catalogo_bytes = $catalogo.Length
    advertencias = @(
        "Revisar y redactar comentarios/roles antes de compartir.",
        "No commitear estos archivos al repositorio.",
        "Un dump no valida por si solo propietarios ni ACL: revisar el catalogo."
    )
} | ConvertTo-Json -Depth 3 | Out-File -Encoding utf8 $rutaResumen

Write-Host "Listo. Revise y redacte antes de compartir:"
Write-Host "  $rutaDump"
Write-Host "  $rutaCatalogo"
Write-Host "  $rutaResumen"
