# Bloque 3 / Paso 3 - consulta SOLO LECTURA sobre copia local.
$ErrorActionPreference = 'Stop'
$hostLocal = '127.0.0.1'
$puerto = 5432
$usuario = 'postgres'
$base = 'fashionstore_restore_20261023_193554_594'
$psql = 'C:\Program Files\PostgreSQL\18\bin\psql.exe'
$archivo = Join-Path $PSScriptRoot '07_verificar_lectura_catalogo_local.sql'

if (-not (Test-Path -LiteralPath $psql -PathType Leaf)) {
  throw 'Falta PostgreSQL 18 local en la ruta esperada.'
}
if (-not (Test-Path -LiteralPath $archivo -PathType Leaf)) {
  throw 'Falta el archivo SQL. Ejecuta git pull.'
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
  Write-Host "Verificando SOLO LECTURA $hostLocal / puerto $puerto / base $base"
  Write-Host 'La transaccion SQL es READ ONLY y termina ROLLBACK.'
  & $psql --host=$hostLocal --port=$puerto --username=$usuario --dbname=$base --no-password --no-psqlrc --set=ON_ERROR_STOP=1 --file=$archivo
  if ($LASTEXITCODE -ne 0) {
    throw 'No se supero la verificacion del catalogo. No avanzar.'
  }
  Write-Host ''
  Write-Host 'LECTURAS CATALOGO LOCAL SUPERADAS (SIN ESCRITURAS)' -ForegroundColor Green
  Write-Host 'No activar SAAS_AUTH_ENABLED ni desplegar Supabase.'
} finally {
  if ($null -eq $passOld) { Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue }
  else { $env:PGPASSWORD = $passOld }
  if ($null -eq $sslOld) { Remove-Item Env:PGSSLMODE -ErrorAction SilentlyContinue }
  else { $env:PGSSLMODE = $sslOld }
}
