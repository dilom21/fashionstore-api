# B3/P5G: preflight READ ONLY de esquema, FK, triggers y RLS para futuros SP.
$ErrorActionPreference = 'Stop'
$servidor = '127.0.0.1'
$puerto = 5432
$usuario = 'postgres'
$base = 'fashionstore_restore_20261023_193554_594'
$psql = 'C:\Program Files\PostgreSQL\18\bin\psql.exe'
$archivo = Join-Path $PSScriptRoot '12_preflight_borradores_reservas_local.sql'
if (-not (Test-Path -LiteralPath $psql -PathType Leaf)) {
    throw 'No se encontro psql PostgreSQL 18 LOCAL.'
}
if (-not (Test-Path -LiteralPath $archivo -PathType Leaf)) {
    throw 'Falta SQL de auditoria; git pull antes.'
}
$passOld = [Environment]::GetEnvironmentVariable('PGPASSWORD','Process')
$sslOld = [Environment]::GetEnvironmentVariable('PGSSLMODE','Process')
$optionsOld = [Environment]::GetEnvironmentVariable('PGOPTIONS','Process')
$clave = Read-Host 'Contrasena PostgreSQL LOCAL (NO Supabase)' -AsSecureString
$ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($clave)
try {
    $env:PGPASSWORD = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)
} finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr)
}
$env:PGSSLMODE = 'disable'
$env:PGOPTIONS = '-c default_transaction_read_only=on -c statement_timeout=30000'
try {
    Write-Host ''
    Write-Host "PREFLIGHT BORRADORES SP SOLO LECTURA: $servidor / $base"
    Write-Host 'NO EJECUTA PROCEDIMIENTOS ni crea tablas, inventario o reservas.'
    & $psql --host=$servidor --port=$puerto --username=$usuario --dbname=$base --no-password --no-psqlrc --set=ON_ERROR_STOP=1 --file=$archivo
    if ($LASTEXITCODE -ne 0) {
        throw 'Preflight de borradores no superado; mantener SP bloqueados.'
    }
    Write-Host ''
    Write-Host 'PREFLIGHT SP TENANT LOCAL SUPERADO (SIN ESCRITURAS)' -ForegroundColor Green
    Write-Host 'Inspeccion de metadatos NO valida los nuevos SP: NO ejecutar borradores.'
} finally {
    if ($null -eq $passOld) { Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue }
    else { $env:PGPASSWORD = $passOld }
    if ($null -eq $optionsOld) { Remove-Item Env:PGOPTIONS -ErrorAction SilentlyContinue }
  else { $env:PGOPTIONS = $optionsOld }
  if ($null -eq $sslOld) { Remove-Item Env:PGSSLMODE -ErrorAction SilentlyContinue }
    else { $env:PGSSLMODE = $sslOld }
}
