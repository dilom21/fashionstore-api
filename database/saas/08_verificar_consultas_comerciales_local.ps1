# Bloque 3 / Paso 5 - comprobacion comercial READ ONLY en DB local.
$ErrorActionPreference = 'Stop'
$servidor = '127.0.0.1'
$puerto = 5432
$usuario = 'postgres'
$base = 'fashionstore_restore_20261023_193554_594'
$psql = 'C:\Program Files\PostgreSQL\18\bin\psql.exe'
$archivo = Join-Path $PSScriptRoot '08_verificar_consultas_comerciales_local.sql'

if (-not (Test-Path -LiteralPath $psql -PathType Leaf)) {
  throw 'Falta PostgreSQL 18 LOCAL en la ruta esperada.'
}
if (-not (Test-Path -LiteralPath $archivo -PathType Leaf)) {
  throw 'Falta el SQL de verificacion. Ejecuta git pull.'
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
  Write-Host "AUDITORIA COMERCIAL SOLO LECTURA: $servidor / puerto $puerto / base $base"
  Write-Host 'BEGIN TRANSACTION READ ONLY; al final ROLLBACK.'
  & $psql --host=$servidor --port=$puerto --username=$usuario --dbname=$base --no-password --no-psqlrc --set=ON_ERROR_STOP=1 --file=$archivo
  if ($LASTEXITCODE -ne 0) {
    throw 'Fallo la auditoria comercial local; revisar antes de avanzar.'
  }
  Write-Host ''
  Write-Host 'AUDITORIA COMERCIAL LOCAL SUPERADA (SIN ESCRITURAS)' -ForegroundColor Green
  Write-Host 'NO abrir operaciones financieras ni activar SAAS_AUTH_ENABLED.'
} finally {
  if ($null -eq $passOld) { Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue }
  else { $env:PGPASSWORD = $passOld }
  if ($null -eq $sslOld) { Remove-Item Env:PGSSLMODE -ErrorAction SilentlyContinue }
  else { $env:PGSSLMODE = $sslOld }
}
