# B3 / P5P: auditoria READ ONLY del bootstrap SaaS, RLS y ACL por columna.
# SOLO sandbox PostgreSQL 18 LOCAL. No crea roles ni inserta datos.
$ErrorActionPreference = 'Stop'
$servidor = '127.0.0.1'
$puerto = 5432
$usuario = 'postgres'
$base = 'fashionstore_saas_reservas_sandbox_5h'
$psql = 'C:\Program Files\PostgreSQL\18\bin\psql.exe'
$sql = Join-Path $PSScriptRoot '21_auditar_bootstrap_rls_permisos_sandbox.sql'

if (-not (Test-Path -LiteralPath $psql -PathType Leaf)) {
    throw 'No se encontro psql PostgreSQL 18 LOCAL.'
}
if (-not (Test-Path -LiteralPath $sql -PathType Leaf)) {
    throw 'Falta auditoria SQL 5P. Ejecutar git pull antes.'
}
if ([string]::IsNullOrWhiteSpace($env:TEMP)) {
    throw 'Directorio TEMP de Windows no disponible.'
}
$salida = Join-Path $env:TEMP 'fashionstore_5p_bootstrap_rls_permisos_sandbox_LOCAL_REVISAR.txt'
if (Test-Path -LiteralPath $salida) {
    throw 'El informe local 5P ya existe en TEMP. Revisarlo antes de repetir; no sobrescribir.'
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
    Write-Host "AUDITORIA BOOTSTRAP JWT Y PERMISOS 5P SOLO LECTURA: $servidor/$base"
    Write-Host 'No lee datos de usuarios ni hashes. No crea roles, grants o policies, no instala SP.'
    & $psql --host=$servidor --port=$puerto --username=$usuario --dbname=$base --no-password --no-psqlrc --set=ON_ERROR_STOP=1 --pset=pager=off --file=$sql |
        Out-File -LiteralPath $salida -Encoding utf8
    if ($LASTEXITCODE -ne 0) {
        Remove-Item -LiteralPath $salida -ErrorAction SilentlyContinue
        throw 'AUDITORIA 5P NO SUPERADA. Ninguna mutacion fue solicitada.'
    }
    $contenido = Get-Content -LiteralPath $salida -Raw
    if ($contenido -notmatch '1_SQL_ROLE_FLAGS' -or
        $contenido -notmatch '2_BOOTSTRAP_RLS' -or
        $contenido -notmatch '4_BOOTSTRAP_COLUMN_RIGHTS' -or
        $contenido -notmatch '(?m)^ROLLBACK\s*$') {
        Remove-Item -LiteralPath $salida -ErrorAction SilentlyContinue
        throw 'Auditoria incompleta: no se detectaron las secciones o ROLLBACK.'
    }
    Write-Host ''
    Write-Host 'AUDITORIA 5P SANDBOX READ ONLY COMPLETADA' -ForegroundColor Green
    Write-Host "Informe local (no subir a GitHub): $salida"
    Write-Host 'El informe enumera permisos y roles SQL, sin filas de usuario ni hashes. Revisar antes de adjuntarlo.'
    Write-Host 'Este reporte NO certifica RLS con un rol restringido. NO crear roles, policies o seed.'
} finally {
    if ($null -eq $passOld) { Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue }
    else { $env:PGPASSWORD = $passOld }
    if ($null -eq $sslOld) { Remove-Item Env:PGSSLMODE -ErrorAction SilentlyContinue }
    else { $env:PGSSLMODE = $sslOld }
    if ($null -eq $optionsOld) { Remove-Item Env:PGOPTIONS -ErrorAction SilentlyContinue }
    else { $env:PGOPTIONS = $optionsOld }
}
