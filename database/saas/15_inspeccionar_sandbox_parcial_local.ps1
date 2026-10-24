# Bloque 3 / Paso 5I: leer metadatos del sandbox en PostgreSQL LOCAL.
# No crea bases, no instala SP, no cambia datos. Requiere sandbox existente.
$ErrorActionPreference = 'Stop'
$servidor = '127.0.0.1'
$puerto = 5432
$usuario = 'postgres'
$base = 'fashionstore_saas_reservas_sandbox_5h'
$psql = 'C:\Program Files\PostgreSQL\18\bin\psql.exe'
$sql = Join-Path $PSScriptRoot '15_inspeccionar_sandbox_parcial_local.sql'

if (-not (Test-Path -LiteralPath $psql -PathType Leaf)) {
    throw 'No se encontro psql PostgreSQL 18 LOCAL.'
}
if (-not (Test-Path -LiteralPath $sql -PathType Leaf)) {
    throw 'Falta el SQL de verificacion 5I. Primero git pull.'
}

$passOld = [Environment]::GetEnvironmentVariable('PGPASSWORD', 'Process')
$sslOld = [Environment]::GetEnvironmentVariable('PGSSLMODE', 'Process')
$optionsOld = [Environment]::GetEnvironmentVariable('PGOPTIONS', 'Process')
$clave = Read-Host 'Contrasena de PostgreSQL LOCAL (NO Supabase)' -AsSecureString
$ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($clave)
try {
    $env:PGPASSWORD = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)
} finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr)
}
$env:PGSSLMODE = 'disable'
$env:PGOPTIONS = '-c default_transaction_read_only=on -c statement_timeout=60000'

try {
    Write-Host ''
    Write-Host "DIAGNOSTICO DEL SANDBOX PARCIAL READ ONLY: $servidor/$base"
    Write-Host 'Solo se cuentan objetos public. No instala, repara ni elimina objetos.'
    & $psql --host=$servidor --port=$puerto --username=$usuario --dbname=$base --no-password --no-psqlrc --set=ON_ERROR_STOP=1 --file=$sql
    if ($LASTEXITCODE -ne 0) {
        throw 'Diagnostico read-only fallido: NO intentar recuperar ni borrar.'
    }
    Write-Host ''
    Write-Host 'INSPECCION SANDBOX PARCIAL FINALIZADA (READ ONLY)' -ForegroundColor Green
    Write-Host 'Solo considerar recuperacion si resultado indica SANDBOX_PARCIAL_VACIO_APTO.'
} finally {
    if ($null -eq $passOld) { Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue }
    else { $env:PGPASSWORD = $passOld }
    if ($null -eq $sslOld) { Remove-Item Env:PGSSLMODE -ErrorAction SilentlyContinue }
    else { $env:PGSSLMODE = $sslOld }
    if ($null -eq $optionsOld) { Remove-Item Env:PGOPTIONS -ErrorAction SilentlyContinue }
    else { $env:PGOPTIONS = $optionsOld }
}
