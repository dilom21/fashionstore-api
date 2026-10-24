# Bloque 3 / Paso 5X: identidad cluster ORIGINAL en sandbox, SOLO LECTURA.
# Nunca conecta Supabase, BD historica ni otro servidor. No ejecuta DDL.
$ErrorActionPreference = 'Stop'
$servidor = '127.0.0.1'
$puerto = 5432
$usuario = 'postgres'
$base = 'fashionstore_saas_reservas_sandbox_5h'
$psql = 'C:\Program Files\PostgreSQL\18\bin\psql.exe'
$sql = Join-Path $PSScriptRoot '28_auditar_identidad_cluster_fuente_5x.sql'

if (-not (Test-Path -LiteralPath $psql -PathType Leaf)) {
    throw '5X: falta psql PG18 local, ninguna auditoria ejecutada.'
}
if (-not (Test-Path -LiteralPath $sql -PathType Leaf)) {
    throw '5X: falta SQL de auditoria. Hacer git pull, sin usar otro SQL.'
}
if ([string]::IsNullOrWhiteSpace($env:TEMP)) {
    throw '5X: TEMP no disponible.'
}
$salida = Join-Path $env:TEMP 'fashionstore_5x_identidad_fuente_LOCAL_REVISAR.txt'
if (Test-Path -LiteralPath $salida) {
    throw '5X: el informe ya existe; revisar antes de repetir. NO sobrescribir.'
}

$passOld = [Environment]::GetEnvironmentVariable('PGPASSWORD', 'Process')
$sslOld = [Environment]::GetEnvironmentVariable('PGSSLMODE', 'Process')
$optionsOld = [Environment]::GetEnvironmentVariable('PGOPTIONS', 'Process')

# No mostrar contraseña; restaurar variables de entorno tras la consulta.
$clave = Read-Host 'Contrasena PostgreSQL LOCAL (NO Supabase)' -AsSecureString
$ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($clave)
try {
    $env:PGPASSWORD = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)
} finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr)
}
$env:PGSSLMODE = 'disable'
$env:PGOPTIONS = '-c default_transaction_read_only=on -c statement_timeout=60000'

try {
    Write-Host '5X IDENTIDAD CLUSTER ORIGINAL: sandbox local READ ONLY'
    Write-Host 'El SQL no imprime la ruta real PGDATA ni lee filas personales.'
    & $psql --host=$servidor --port=$puerto --username=$usuario --dbname=$base --no-password --no-psqlrc --set=ON_ERROR_STOP=1 --pset=pager=off --file=$sql |
        Out-File -LiteralPath $salida -Encoding utf8
    if ($LASTEXITCODE -ne 0) {
        Remove-Item -LiteralPath $salida -ErrorAction SilentlyContinue
        throw '5X: psql devolvio error; ninguna mutacion fue solicitada.'
    }
    $reporte = Get-Content -LiteralPath $salida -Raw
    if ($reporte -notmatch '1_IDENTIDAD_CLUSTER_FUENTE' -or
        $reporte -notmatch '2_DICTAMEN_ALCANCE' -or
        $reporte -notmatch 'RUTA_PGDATA_ORIGEN_NO_PUBLICADA' -or
        $reporte -notmatch 'ORIGEN_MEDIDO_DESTINO_AUN_INEXISTENTE' -or
        $reporte -notmatch '(?m)^ROLLBACK\s*$') {
        Remove-Item -LiteralPath $salida -ErrorAction SilentlyContinue
        throw '5X: auditoria incompleta; no hay resultados verificables.'
    }
    Write-Host '5X AUDITORIA IDENTIDAD FUENTE READ ONLY COMPLETADA' -ForegroundColor Green
    Write-Host "Informe privado local (no subir GitHub): $salida"
    Write-Host 'Contiene el ID del cluster fuente: revisar antes de compartir o redactar.'
    Write-Host '5X DESTINO=NO_EXISTE; RLS_A_B=NO_CERTIFICADO; NO crear cluster/roles/policies.'
} finally {
    if ($null -eq $passOld) { Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue }
    else { $env:PGPASSWORD = $passOld }
    if ($null -eq $sslOld) { Remove-Item Env:PGSSLMODE -ErrorAction SilentlyContinue }
    else { $env:PGSSLMODE = $sslOld }
    if ($null -eq $optionsOld) { Remove-Item Env:PGOPTIONS -ErrorAction SilentlyContinue }
    else { $env:PGOPTIONS = $optionsOld }
}
