# B3 / P5K: auditoria READ ONLY de cuerpos trigger, grants y secuencias.
# SOLO sandbox PostgreSQL 18 LOCAL. No crea roles ni inserta datos.
$ErrorActionPreference = 'Stop'
$servidor = '127.0.0.1'
$puerto = 5432
$usuario = 'postgres'
$base = 'fashionstore_saas_reservas_sandbox_5h'
$psql = 'C:\Program Files\PostgreSQL\18\bin\psql.exe'
$sql = Join-Path $PSScriptRoot '18_auditar_cuerpos_triggers_y_privilegios_sandbox.sql'

if (-not (Test-Path -LiteralPath $psql -PathType Leaf)) {
    throw 'No se encontro psql PostgreSQL 18 LOCAL.'
}
if (-not (Test-Path -LiteralPath $sql -PathType Leaf)) {
    throw 'Falta auditoria SQL 5K. Ejecutar git pull antes.'
}
if ([string]::IsNullOrWhiteSpace($env:TEMP)) {
    throw 'Directorio TEMP de Windows no disponible.'
}
$salida = Join-Path $env:TEMP 'fashionstore_5k_triggers_privilegios_sandbox_LOCAL_REVISAR.txt'
if (Test-Path -LiteralPath $salida) {
    throw 'El informe local 5K ya existe en TEMP. Revisarlo antes de repetir; no sobrescribir.'
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
$env:PGOPTIONS = '-c default_transaction_read_only=on -c statement_timeout=60000'

try {
    Write-Host ''
    Write-Host "AUDITORIA TRIGGERS Y ACL 5K SOLO LECTURA: $servidor/$base"
    Write-Host 'No ejecuta triggers, no crea roles, no instala SP ni altera ninguna base.'
    & $psql --host=$servidor --port=$puerto --username=$usuario --dbname=$base --no-password --no-psqlrc --set=ON_ERROR_STOP=1 --pset=pager=off --file=$sql |
        Out-File -LiteralPath $salida -Encoding utf8
    if ($LASTEXITCODE -ne 0) {
        Remove-Item -LiteralPath $salida -ErrorAction SilentlyContinue
        throw 'AUDITORIA 5K NO SUPERADA. Ninguna mutacion fue solicitada.'
    }
    $contenido = Get-Content -LiteralPath $salida -Raw
    if ($contenido -notmatch '1_TRIGGER_FUNCTIONS' -or
        $contenido -notmatch '2_FUNCTION_BODIES' -or
        $contenido -notmatch '3_ROUTINE_EXECUTE_PUBLIC' -or
        $contenido -notmatch '(?m)^ROLLBACK\s*$') {
        Remove-Item -LiteralPath $salida -ErrorAction SilentlyContinue
        throw 'Auditoria incompleta: no se detectaron las secciones o ROLLBACK.'
    }
    Write-Host ''
    Write-Host 'AUDITORIA 5K SANDBOX READ ONLY COMPLETADA' -ForegroundColor Green
    Write-Host "Informe local (no subir a GitHub): $salida"
    Write-Host 'Revisar el archivo por definiciones o literales sensibles antes de adjuntarlo.'
    Write-Host 'RLS con postgres NO certifica aislamiento con cuenta API. NO ejecutar seed.'
} finally {
    if ($null -eq $passOld) { Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue }
    else { $env:PGPASSWORD = $passOld }
    if ($null -eq $sslOld) { Remove-Item Env:PGSSLMODE -ErrorAction SilentlyContinue }
    else { $env:PGSSLMODE = $sslOld }
    if ($null -eq $optionsOld) { Remove-Item Env:PGOPTIONS -ErrorAction SilentlyContinue }
    else { $env:PGOPTIONS = $optionsOld }
}
