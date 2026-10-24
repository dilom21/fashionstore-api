# Bloque 3 / Paso 5F: exportar a TEMP las definiciones PG de reservas.
# SOLO LECTURA. Nunca crea, altera ni invoca un procedimiento.
$ErrorActionPreference = 'Stop'
$hostLocal = '127.0.0.1'
$puerto = 5432
$usuario = 'postgres'
$bd = 'fashionstore_restore_20261023_193554_594'
$psql = 'C:\Program Files\PostgreSQL\18\bin\psql.exe'
$sql = Join-Path $PSScriptRoot '11_extraer_definiciones_reserva_solo_lectura.sql'
$salida = Join-Path $env:TEMP 'fashionstore_P5F_SP_reservas_LOCAL_REVISAR.txt'

if (-not (Test-Path -LiteralPath $psql -PathType Leaf)) {
    throw 'No se encontro psql PostgreSQL 18 LOCAL.'
}
if (-not (Test-Path -LiteralPath $sql -PathType Leaf)) {
    throw 'Falta el SQL 5F; ejecuta git pull primero.'
}
if ([string]::IsNullOrWhiteSpace($env:TEMP)) {
    throw 'No se pudo localizar el directorio temporal de Windows.'
}

$oldPassword = [Environment]::GetEnvironmentVariable('PGPASSWORD', 'Process')
$oldSSL = [Environment]::GetEnvironmentVariable('PGSSLMODE', 'Process')
$oldOptions = [Environment]::GetEnvironmentVariable('PGOPTIONS', 'Process')
$clave = Read-Host 'Contrasena PostgreSQL LOCAL (NO Supabase)' -AsSecureString
$ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($clave)
try {
    $env:PGPASSWORD = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)
} finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr)
}
$env:PGSSLMODE = 'disable'
# Defensa adicional: toda la conexion esta restringida a READ ONLY.
$env:PGOPTIONS = '-c default_transaction_read_only=on -c statement_timeout=30000'

try {
    Write-Host ''
    Write-Host "Extraccion de DEFINICIONES (sin CALL): $hostLocal / $bd"
    Write-Host 'Se exporta texto SQL solo a TEMP local, FUERA del repositorio GitHub.'
    # stdout -> archivo privado del dispositivo, stderr -> consola.
    # PSQL no ejecuta los CREATE PROCEDURE que devuelve pg_get_functiondef.
    & $psql --host=$hostLocal --port=$puerto --username=$usuario --dbname=$bd --no-password --no-psqlrc --set=ON_ERROR_STOP=1 --file=$sql |
        Out-File -LiteralPath $salida -Encoding utf8
    if ($LASTEXITCODE -ne 0) {
        Remove-Item -LiteralPath $salida -ErrorAction SilentlyContinue
        throw 'Extraccion local fallida; verificar mensajes de psql.'
    }
    $contenido = Get-Content -LiteralPath $salida -Raw
    if ($contenido -notmatch 'INICIO (?:public\.)?sp_crear_reserva_desde_carrito\(' -or
        $contenido -notmatch 'INICIO (?:public\.)?sp_cancelar_reserva\(' -or
        $contenido -notmatch 'INICIO (?:public\.)?sp_finalizar_reserva_sin_compra\(' -or
        $contenido -notmatch '(?m)^ROLLBACK\s*$') {
        Remove-Item -LiteralPath $salida -ErrorAction SilentlyContinue
        throw 'Extraccion incompleta: falta un procedimiento o ROLLBACK.'
    }

    Write-Host ''
    Write-Host 'EXTRACCION LOCAL SUPERADA (SIN ESCRITURAS)' -ForegroundColor Green
    Write-Host "Archivo generado: $salida"
    Write-Host 'REVISA EL ARCHIVO y elimina cualquier dato secreto antes de adjuntarlo al chat.'
    Write-Host 'No subir el archivo a GitHub, no ejecutar los CREATE PROCEDURE exportados.'
} finally {
    if ($null -eq $oldPassword) { Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue }
    else { $env:PGPASSWORD = $oldPassword }
    if ($null -eq $oldSSL) { Remove-Item Env:PGSSLMODE -ErrorAction SilentlyContinue }
    else { $env:PGSSLMODE = $oldSSL }
    if ($null -eq $oldOptions) { Remove-Item Env:PGOPTIONS -ErrorAction SilentlyContinue }
    else { $env:PGOPTIONS = $oldOptions }
}
