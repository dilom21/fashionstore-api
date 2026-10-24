# B3/P5E: inspeccion READ ONLY de firmas SP CU16/CU18.
$ErrorActionPreference = 'Stop'
$servidor = '127.0.0.1'
$puerto = 5432
$usuario = 'postgres'
$base = 'fashionstore_restore_20261023_193554_594'
$psql = 'C:\Program Files\PostgreSQL\18\bin\psql.exe'
$archivo = Join-Path $PSScriptRoot '10_auditar_procedimientos_reservas_local.sql'
if (-not (Test-Path -LiteralPath $psql -PathType Leaf)) {
    throw 'No se encontro psql PostgreSQL 18 LOCAL.'
}
if (-not (Test-Path -LiteralPath $archivo -PathType Leaf)) {
    throw 'Falta SQL de auditoria; git pull antes.'
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
    Write-Host "AUDITORIA DE SP RESERVAS SOLO LECTURA: $servidor / $base"
    Write-Host 'NO EJECUTA NINGUN PROCEDIMIENTO ni modifica stock o reservas.'
    & $psql --host=$servidor --port=$puerto --username=$usuario --dbname=$base --no-password --no-psqlrc --set=ON_ERROR_STOP=1 --file=$archivo
    if ($LASTEXITCODE -ne 0) {
        throw 'Auditoria de metadatos de reserva no superada; mantener SP bloqueados.'
    }
    Write-Host ''
    Write-Host 'AUDITORIA SP RESERVAS LOCAL SUPERADA (SIN ESCRITURAS)' -ForegroundColor Green
    Write-Host 'Inspeccion de firmas NO certifica aislamiento: NO activar reserva SaaS.'
} finally {
    if ($null -eq $passOld) { Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue }
    else { $env:PGPASSWORD = $passOld }
    if ($null -eq $sslOld) { Remove-Item Env:PGSSLMODE -ErrorAction SilentlyContinue }
    else { $env:PGSSLMODE = $sslOld }
}
