# B3/P5B: verificacion preflight SOLO LECTURA de carrito/stock local.
$ErrorActionPreference = 'Stop'
$servidor = '127.0.0.1'
$puerto = 5432
$usuario = 'postgres'
$base = 'fashionstore_restore_20261023_193554_594'
$psql = 'C:\Program Files\PostgreSQL\18\bin\psql.exe'
$archivo = Join-Path $PSScriptRoot '09_preflight_carrito_tenant_local.sql'

if (-not (Test-Path -LiteralPath $psql -PathType Leaf)) {
  throw 'No se encontro psql PostgreSQL 18 LOCAL.'
}
if (-not (Test-Path -LiteralPath $archivo -PathType Leaf)) {
  throw 'Falta SQL de preflight: primero git pull.'
}

$passOld = [Environment]::GetEnvironmentVariable('PGPASSWORD','Process')
$sslOld = [Environment]::GetEnvironmentVariable('PGSSLMODE','Process')
$clave = Read-Host 'Contrasena PostgreSQL LOCAL (NO Supabase)' -AsSecureString
$ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($clave)
try {
  $env:PGPASSWORD = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)
} finally {
  [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr)
}
$env:PGSSLMODE = 'disable'

try {
  Write-Host ''
  Write-Host "PREFLIGHT CARRITO SOLO LECTURA: $servidor / puerto $puerto / base $base"
  Write-Host 'Solo BEGIN TRANSACTION READ ONLY y ROLLBACK; no INSERT ni UPDATE.'
  & $psql --host=$servidor --port=$puerto --username=$usuario --dbname=$base --no-password --no-psqlrc --set=ON_ERROR_STOP=1 --file=$archivo
  if ($LASTEXITCODE -ne 0) {
    throw 'Preflight de carrito no superado. No habilitar ningun endpoint.'
  }
  Write-Host ''
  Write-Host 'PREFLIGHT CARRITO LOCAL SUPERADO (SIN ESCRITURAS)' -ForegroundColor Green
  Write-Host 'No activar SAAS_AUTH_ENABLED ni desplegar Supabase.'
} finally {
  if ($null -eq $passOld) { Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue }
  else { $env:PGPASSWORD = $passOld }
  if ($null -eq $sslOld) { Remove-Item Env:PGSSLMODE -ErrorAction SilentlyContinue }
  else { $env:PGSSLMODE = $sslOld }
}
