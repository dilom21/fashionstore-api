#Requires -Version 5.1
# B3/5AD - Inspeccion de esquema para completar 14 casos A/B reales.
# SOLO LABORATORIO 127.0.0.1:55432, NO lee contenido de filas.
# Genera archivo TEMP LOCAL con columnas, FK, CHECK, triggers y CONTEOS.
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
$root='C:\SI2_Parcial1\fashionstore-api'
$psql='C:\Program Files\PostgreSQL\18\bin\psql.exe'
$sql=Join-Path $root 'database\saas\36_inventario_esquema_para_casos_ab_5ad_SOLO_LECTURA.sql'
$oldPassword=[Environment]::GetEnvironmentVariable('PGPASSWORD','Process')
$oldOpts=[Environment]::GetEnvironmentVariable('PGOPTIONS','Process')
$oldSsl=[Environment]::GetEnvironmentVariable('PGSSLMODE','Process')
$oldTimeout=[Environment]::GetEnvironmentVariable('PGCONNECT_TIMEOUT','Process')
function Block([string]$reason){throw ("5AD BLOQUEADO: "+$reason)}
try {
  if ((Get-Location).Path -ne $root){Block 'Directorio proyecto incorrecto'}
  if ((git branch --show-current).Trim() -ne 'feat/saas-multitenant-parcial2'){Block 'Rama incorrecta'}
  if (git status --porcelain){Block 'Cambios locales pendientes'}
  if (-not(Test-Path -LiteralPath $psql -PathType Leaf)){Block 'psql PG18 inexistente'}
  if (-not(Test-Path -LiteralPath $sql -PathType Leaf)){Block 'SQL 5AD no encontrado'}
  [Environment]::SetEnvironmentVariable('PGPASSWORD',$null,'Process')
  [Environment]::SetEnvironmentVariable('PGOPTIONS','-c default_transaction_read_only=on','Process')
  [Environment]::SetEnvironmentVariable('PGSSLMODE','disable','Process')
  [Environment]::SetEnvironmentVariable('PGCONNECT_TIMEOUT','8','Process')
  $file=Join-Path $env:TEMP ('fashionstore_5ad_estructura_lab_'+[guid]::NewGuid().ToString('N')+'_LOCAL_REVISAR.txt')
  Write-Host '5AD INVENTARIO SQL SOLO LECTURA - NO INSERT/CREATE/ALTER'
  Write-Host '5AD Se solicita solo la clave del postgres del LABORATORIO 55432'
  & $psql -X -W -v 'ON_ERROR_STOP=1' -P pager=off -h 127.0.0.1 -p 55432 -U postgres -d fashionstore_rls_lab_5u -f $sql -o $file
  if ($LASTEXITCODE -ne 0){Block 'Consulta no valida: informe incompleto. NO ejecutar otros scripts.'}
  $contents=[IO.File]::ReadAllText($file)
  if (-not $contents.Contains('5AD_PREFLIGHT_OK_46_TABLAS_48_FK_35_POLICIES_2_EMPRESAS')){
    Block 'El resultado no contiene la confirmacion de preflight'
  }
  Write-Host '5AD OK: inventario de columnas, FK, CHECK, triggers y conteos generado.'
  Write-Host '5AD OK: transaccion READ ONLY finalizada con ROLLBACK'
  Write-Host ('5AD ARCHIVO LOCAL (adjuntar al chat tras revisarlo): '+$file)
  Write-Host '5AD SIN CAMBIOS: no se instalaron nuevas policies ni datos ficticios'
} finally {
  [Environment]::SetEnvironmentVariable('PGPASSWORD',$oldPassword,'Process')
  [Environment]::SetEnvironmentVariable('PGOPTIONS',$oldOpts,'Process')
  [Environment]::SetEnvironmentVariable('PGSSLMODE',$oldSsl,'Process')
  [Environment]::SetEnvironmentVariable('PGCONNECT_TIMEOUT',$oldTimeout,'Process')
}
