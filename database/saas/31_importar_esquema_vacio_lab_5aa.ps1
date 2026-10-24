#Requires -Version 5.1
# B3 / 5AA: importacion SOLO-ESQUEMA sandbox 5432 -> laboratorio 55432.
# No importa registros historicos, no modifica servidor original ni Supabase.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$root = 'C:\SI2_Parcial1\fashionstore-api'
$bin = 'C:\Program Files\PostgreSQL\18\bin'
$sourceDb = 'fashionstore_saas_reservas_sandbox_5h'
$targetDb = 'fashionstore_rls_lab_5u'
$targetData = 'C:\SI2_Parcial1\fashionstore-rls-lab-5v-data'
$psql = Join-Path $bin 'psql.exe'
$dump = Join-Path $bin 'pg_dump.exe'
$restore = Join-Path $bin 'pg_restore.exe'
$oldPassword = [Environment]::GetEnvironmentVariable('PGPASSWORD', 'Process')
$oldOpts = [Environment]::GetEnvironmentVariable('PGOPTIONS', 'Process')
$oldSsl = [Environment]::GetEnvironmentVariable('PGSSLMODE', 'Process')
$oldTimeout = [Environment]::GetEnvironmentVariable('PGCONNECT_TIMEOUT', 'Process')
$work = $null

function Stop-Lab([string] $reason) { throw ('5AA BLOQUEADO: ' + $reason) }
function Set-Password([Security.SecureString] $secret) {
    $ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secret)
    try {
        [Environment]::SetEnvironmentVariable('PGPASSWORD',
            [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr), 'Process')
    } finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr)
    }
}
function Get-Scalar([int] $port, [string] $db, [string] $sql) {
    $res = @(& $psql -X -q -A -t -w -v 'ON_ERROR_STOP=1' -h '127.0.0.1' -p "$port" -U postgres -d $db -c $sql)
    if ($LASTEXITCODE -ne 0 -or $res.Count -ne 1) {
        Stop-Lab "Respuesta o conexion SQL inesperada: puerto $port"
    }
    return ([string]$res[0]).Trim()
}
function Assert-Exit([string] $description) {
    if ($LASTEXITCODE -ne 0) { Stop-Lab $description }
}
function Canon([string] $path) {
    return [IO.Path]::GetFullPath($path).TrimEnd([char]'\').ToLowerInvariant()
}

try {
    Write-Host '5AA [1/6] Verificar herramientas/entorno; no borrar ni sobrescribir'
    if ((Get-Location).Path -ne $root) { Stop-Lab "Abrir PowerShell desde $root" }
    foreach ($tool in @($psql, $dump, $restore)) {
        if (-not (Test-Path -LiteralPath $tool -PathType Leaf)) {
            Stop-Lab 'Faltan ejecutables PostgreSQL 18'
        }
    }
    if (-not (Test-Path -LiteralPath (Join-Path $targetData 'PG_VERSION'))) {
        Stop-Lab 'Laboratorio 5Z inexistente; no ejecutar initdb otra vez'
    }
    [Environment]::SetEnvironmentVariable('PGSSLMODE', 'disable', 'Process')
    [Environment]::SetEnvironmentVariable('PGCONNECT_TIMEOUT', '8', 'Process')

    Write-Host '5AA [2/6] Preflight de fuente 5432 SOLO LECTURA'
    $secretSrc = Read-Host 'Contrasena LOCAL original postgres (5432)' -AsSecureString
    Set-Password $secretSrc
    Remove-Variable secretSrc
    [Environment]::SetEnvironmentVariable('PGOPTIONS', '-c default_transaction_read_only=on', 'Process')
    $sqlSrc = @'
SELECT (SELECT system_identifier::text FROM pg_control_system()) || '|' ||
 current_database() || '|' || inet_server_port()::text || '|' ||
 (SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
    WHERE n.nspname='public' AND c.relkind='r')::text || '|' ||
 (SELECT count(*) FROM pg_constraint co
    JOIN pg_class c ON c.oid=co.conrelid JOIN pg_namespace n ON n.oid=c.relnamespace
    WHERE n.nspname='public' AND co.contype='f' AND co.convalidated
      AND co.conname LIKE 'fk_tenant_%')::text;
'@
    $src = (Get-Scalar 5432 $sourceDb $sqlSrc) -split '\|'
    if ($src.Count -ne 5 -or $src[0] -notmatch '^[0-9]+$' -or
        $src[1] -ne $sourceDb -or $src[2] -ne '5432' -or
        $src[3] -ne '46' -or $src[4] -ne '48') {
        Stop-Lab 'Sandbox fuente diferente: abortar sin exportar'
    }

    Write-Host '5AA [3/6] Exportar pg_dump SOLO esquema, nunca datos'
    $work = Join-Path $env:TEMP ('fashionstore_5aa_' + [guid]::NewGuid().ToString('N'))
    [IO.Directory]::CreateDirectory($work) | Out-Null
    $archive = Join-Path $work 'esquema_vacio.dump'
    $list = Join-Path $work 'toc_seguro.txt'
    & $dump -w -h 127.0.0.1 -p 5432 -U postgres -d $sourceDb --format=custom --schema-only --schema=public --no-owner --no-acl --file=$archive
    Assert-Exit 'pg_dump fallo; origen no modificado'
    $toc = @(& $restore --list $archive)
    Assert-Exit 'pg_restore --list fallo'
    $entries = @($toc | Where-Object { $_ -match '^\d+;' })
    $tableCount = @($entries | Where-Object { $_ -match '^\d+;\s+\d+\s+\d+\s+TABLE public ' }).Count
    if ($tableCount -ne 46) {
        Stop-Lab "El archivo contiene $tableCount tablas; esperadas 46"
    }
    $banned = @($entries | Where-Object {
        $_ -match '\s(TABLE DATA|MATERIALIZED VIEW DATA|BLOB|BLOBS|ACL|DEFAULT ACL|POLICY|SUBSCRIPTION|PUBLICATION|EVENT TRIGGER|USER MAPPING|FOREIGN SERVER)\s'
    })
    if ($banned.Count -gt 0) {
        Stop-Lab 'Se encontro objeto excluido del contrato de importacion; revisar antes'
    }
    # template0 ya contiene el schema public. Evitar CREATE SCHEMA public.
    $filtered = @($toc | Where-Object {
        $_ -notmatch '^\d+;\s+\d+\s+\d+\s+SCHEMA\s+-\s+public\s' -and
        $_ -notmatch '^\d+;\s+\d+\s+\d+\s+COMMENT\s+-\s+SCHEMA public\s'
    })
    [IO.File]::WriteAllLines($list, [string[]]$filtered, (New-Object System.Text.UTF8Encoding($false)))
    [Environment]::SetEnvironmentVariable('PGPASSWORD', $null, 'Process')
    [Environment]::SetEnvironmentVariable('PGOPTIONS', $null, 'Process')

    Write-Host '5AA [4/6] Preflight del cluster nuevo 55432'
    $secretDst = Read-Host 'Contrasena NUEVA laboratorio postgres (55432)' -AsSecureString
    Set-Password $secretDst
    Remove-Variable secretDst
    $sqlDst = @'
SELECT (SELECT system_identifier::text FROM pg_control_system()) || '|' ||
 current_setting('data_directory') || '|' || inet_server_port()::text || '|' ||
 current_setting('listen_addresses') || '|' ||
 (SELECT count(*) FROM pg_database WHERE datname='fashionstore_rls_lab_5u')::text
'@
    $dst = (Get-Scalar 55432 'postgres' $sqlDst) -split '\|'
    if ($dst.Count -ne 5 -or $dst[0] -notmatch '^[0-9]+$' -or
        $dst[0] -eq $src[0] -or (Canon $dst[1]) -ne (Canon $targetData) -or
        $dst[2] -ne '55432' -or $dst[3] -ne '127.0.0.1') {
        Stop-Lab 'El destino no coincide con cluster independiente 5Z'
    }
    if ($dst[4] -ne '0') {
        Stop-Lab 'La base del laboratorio YA existe; no sobrescribir ni reimportar'
    }
    if ((Get-Scalar 55432 'postgres' "SELECT current_setting('password_encryption')") -ne 'scram-sha-256') {
        Stop-Lab 'Password encryption no es SCRAM'
    }

    Write-Host '5AA [5/6] Crear base VACIA solo en 55432 e importar esquema'
    & $psql -X -w -v 'ON_ERROR_STOP=1' -h 127.0.0.1 -p 55432 -U postgres -d postgres -c "CREATE DATABASE fashionstore_rls_lab_5u TEMPLATE template0 ENCODING 'UTF8';"
    Assert-Exit 'Fallo CREATE DATABASE, no continuar'
    & $restore -w -h 127.0.0.1 -p 55432 -U postgres -d $targetDb --use-list=$list --single-transaction --exit-on-error --no-owner --no-acl $archive
    Assert-Exit 'Fallo importar esquema; base lab retenida para diagnostico, NO reintentar'

    # Cerrar EXECUTE heredado y permisos PUBLIC exclusivamente en laboratorio.
    $sqlLock = @'
BEGIN;
REVOKE ALL ON SCHEMA public FROM PUBLIC;
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM PUBLIC;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM PUBLIC;
REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA public FROM PUBLIC;
REVOKE EXECUTE ON ALL PROCEDURES IN SCHEMA public FROM PUBLIC;
COMMIT;
'@
    & $psql -X -w -v 'ON_ERROR_STOP=1' -h 127.0.0.1 -p 55432 -U postgres -d $targetDb -c $sqlLock
    Assert-Exit 'Error de cierre de EXECUTE/ACL en base laboratorio'

    Write-Host '5AA [6/6] Verificar tablas y emitir esquema de entidades clave'
    $after = Get-Scalar 55432 $targetDb @'
SELECT (SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
 WHERE n.nspname='public' AND c.relkind='r')::text || '|' ||
 (SELECT count(*) FROM pg_constraint co JOIN pg_class c ON c.oid=co.conrelid
 JOIN pg_namespace n ON n.oid=c.relnamespace
 WHERE n.nspname='public' AND co.contype='f' AND co.convalidated
 AND co.conname LIKE 'fk_tenant_%')::text
'@
    if ($after -ne '46|48') { Stop-Lab "Estructura importada diferente de 46|48: $after" }
    $columns = @'
SELECT table_name || ': ' ||
 string_agg(column_name || ' ' || udt_name ||
  CASE WHEN is_nullable='NO' THEN ' NOT_NULL' ELSE '' END ||
  CASE WHEN column_default IS NOT NULL THEN ' DEFAULT' ELSE '' END,
  ', ' ORDER BY ordinal_position)
FROM information_schema.columns
WHERE table_schema='public'
AND table_name IN ('empresa','plan_saas','suscripcion','usuario','usuario_empresa',
 'cliente','sucursal','carrito','detalle_carrito','inventario','reserva',
 'detalle_reserva','bitacora')
GROUP BY table_name ORDER BY table_name;
'@
    $lines = @(& $psql -X -q -A -t -w -v 'ON_ERROR_STOP=1' -h 127.0.0.1 -p 55432 -U postgres -d $targetDb -c $columns)
    Assert-Exit 'No se pudieron leer columnas de diagnostico'
    Write-Host '5AA COLUMNAS ESTRUCTURALES (sin datos personales):'
    foreach ($line in $lines) { Write-Host $line }
    Write-Host '5AA OK: BASE NUEVA + SOLO ESTRUCTURA, 46 TABLAS, 48 FK TENANT'
    Write-Host '5AA OK: SIN FILAS EXPORTADAS; ACL PUBLIC CERRADA EN LAB'
    Write-Host '5AA PENDIENTE: roles/restricciones tenant y pruebas A01-A14'
} finally {
    [Environment]::SetEnvironmentVariable('PGPASSWORD', $oldPassword, 'Process')
    [Environment]::SetEnvironmentVariable('PGOPTIONS', $oldOpts, 'Process')
    [Environment]::SetEnvironmentVariable('PGSSLMODE', $oldSsl, 'Process')
    [Environment]::SetEnvironmentVariable('PGCONNECT_TIMEOUT', $oldTimeout, 'Process')
    if ($null -ne $work -and (Test-Path -LiteralPath $work -PathType Container) -and
        $work.StartsWith((Join-Path $env:TEMP 'fashionstore_5aa_'), [StringComparison]::OrdinalIgnoreCase)) {
        Remove-Item -LiteralPath $work -Recurse -Force
    }
}
