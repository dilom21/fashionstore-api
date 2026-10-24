# B3 / P5L: auditoria READ ONLY de BITACORA, trigger IP y RLS.
# SOLO sandbox PostgreSQL 18 LOCAL. No crea roles ni inserta datos.
$ErrorActionPreference = 'Stop'
$servidor = '127.0.0.1'
$puerto = 5432
$usuario = 'postgres'
$base = 'fashionstore_saas_reservas_sandbox_5h'
$psql = 'C:\Program Files\PostgreSQL\18\bin\psql.exe'
$sql = Join-Path $PSScriptRoot '19_auditar_bitacora_y_rls_sandbox.sql'

if (-not (Test-Path -LiteralPath $psql -PathType Leaf)) {
    throw 'No se encontro psql PostgreSQL 18 LOCAL.'
}
if (-not (Test-Path -LiteralPath $sql -PathType Leaf)) {
    throw 'Falta auditoria SQL 5L. Ejecutar git pull antes.'
}
if ([string]::IsNullOrWhiteSpace($env:TEMP)) {
    throw 'Directorio TEMP de Windows no disponible.'
}
$salida = Join-Path $env:TEMP 'fashionstore_5l_bitacora_rls_sandbox_LOCAL_REVISAR.txt'
if (Test-Path -LiteralPath $salida) {
    throw 'El informe local 5L ya existe en TEMP. Revisarlo antes de repetir; no sobrescribir.'
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
    Write-Host "AUDITORIA BITACORA Y RLS 5L SOLO LECTURA: $servidor/$base"
    Write-Host 'No ejecuta funciones, no crea roles/policies, no instala SP ni altera ninguna base.'
    & $psql --host=$servidor --port=$puerto --username=$usuario --dbname=$base --no-password --no-psqlrc --set=ON_ERROR_STOP=1 --pset=pager=off --file=$sql |
        Out-File -LiteralPath $salida -Encoding utf8
    if ($LASTEXITCODE -ne 0) {
        Remove-Item -LiteralPath $salida -ErrorAction SilentlyContinue
        throw 'AUDITORIA 5L NO SUPERADA. Ninguna mutacion fue solicitada.'
    }
    $contenido = Get-Content -LiteralPath $salida -Raw
    if ($contenido -notmatch '1_BITACORA_COLUMNS' -or
        $contenido -notmatch '4_AUDIT_RLS_STATUS' -or
        $contenido -notmatch '8_AUDIT_FUNCTION_BODIES' -or
        $contenido -notmatch '(?m)^ROLLBACK\s*$') {
        Remove-Item -LiteralPath $salida -ErrorAction SilentlyContinue
        throw 'Auditoria incompleta: no se detectaron las secciones o ROLLBACK.'
    }
    Write-Host ''
    Write-Host 'AUDITORIA 5L SANDBOX READ ONLY COMPLETADA' -ForegroundColor Green
    Write-Host "Informe local (no subir a GitHub): $salida"
    Write-Host 'Revisar el archivo por definiciones o literales sensibles antes de adjuntarlo.'
    Write-Host 'El informe NO certifica RLS con rol restringido. NO crear roles ni seed.'
} finally {
    if ($null -eq $passOld) { Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue }
    else { $env:PGPASSWORD = $passOld }
    if ($null -eq $sslOld) { Remove-Item Env:PGSSLMODE -ErrorAction SilentlyContinue }
    else { $env:PGSSLMODE = $sslOld }
    if ($null -eq $optionsOld) { Remove-Item Env:PGOPTIONS -ErrorAction SilentlyContinue }
    else { $env:PGOPTIONS = $optionsOld }
}
