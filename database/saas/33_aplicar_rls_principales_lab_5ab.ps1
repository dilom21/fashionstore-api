#Requires -Version 5.1
# FashionStore B3/5AB: A/B SQL autentico SOLO laboratorio 127.0.0.1:55432.
# Nunca ejecutar contra 5432, Supabase o PGDATA anterior.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$root = 'C:\SI2_Parcial1\fashionstore-api'
$bin = 'C:\Program Files\PostgreSQL\18\bin'
$psql = Join-Path $bin 'psql.exe'
$script = Join-Path $root 'database\saas\32_instalar_principales_policies_rls_5ab.sql'
$db = 'fashionstore_rls_lab_5u'
$data = 'C:\SI2_Parcial1\fashionstore-rls-lab-5v-data'
$roleA = 'fashionstore_5ab_a'
$roleB = 'fashionstore_5ab_b'
$oldPassword = [Environment]::GetEnvironmentVariable('PGPASSWORD','Process')
$oldOpts = [Environment]::GetEnvironmentVariable('PGOPTIONS','Process')
$oldSsl = [Environment]::GetEnvironmentVariable('PGSSLMODE','Process')
$oldTimeout = [Environment]::GetEnvironmentVariable('PGCONNECT_TIMEOUT','Process')

function Stop-Lab([string] $reason) { throw ('5AB BLOQUEADO: '+$reason) }
function Set-Password([Security.SecureString] $secret) {
    $ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secret)
    try {
        [Environment]::SetEnvironmentVariable('PGPASSWORD',
            [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr), 'Process')
    } finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr) }
}
function Get-One([string] $who,[string] $query) {
    $items = @(& $psql -X -q -A -t -w -v 'ON_ERROR_STOP=1' -h 127.0.0.1 -p 55432 -U $who -d $db -c $query)
    if ($LASTEXITCODE -ne 0 -or $items.Count -ne 1) { Stop-Lab 'Fallo preflight SQL local' }
    return [string]$items[0]
}
function Check-Role([string] $name,[string] $slug,[string] $otherId) {
    Write-Host ("5AB Probando login autentico " + $name + " (solicita su clave)")
    # La GUC se falsifica al valor exacto de la OTRA empresa.
    # NO es usada como fuente de identidad para policies SQL.
    $q = @"
SET app.empresa_id = '$otherId';
SET app.usuario_id = '999999';
SELECT session_user || '|' ||
 row_security_active('public.empresa'::regclass)::text || '|' ||
 coalesce((SELECT string_agg(slug, ',' ORDER BY slug) FROM public.empresa), '') || '|' ||
 has_column_privilege(session_user, 'public.usuario', 'password_hash', 'SELECT')::text || '|' ||
 (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
    WHERE n.nspname='public'
      AND p.proname IN ('sp_cancelar_reserva','sp_crear_reserva',
                       'sp_crear_reserva_desde_carrito','sp_finalizar_reserva_sin_compra')
      AND has_function_privilege(session_user,p.oid,'EXECUTE'))::text || '|' ||
 (row_security_active('public.carrito'::regclass) AND
  row_security_active('public.inventario'::regclass) AND
  row_security_active('public.reserva'::regclass) AND
  row_security_active('public.bitacora'::regclass))::text;
"@
    $rows = @(& $psql -X -q -A -t -W -v 'ON_ERROR_STOP=1' -h 127.0.0.1 -p 55432 -U $name -d $db -c $q)
    if ($LASTEXITCODE -ne 0) { Stop-Lab "Fallo autenticar o consultar como $name" }
    $result = @($rows | Where-Object { $_ -like "$name|*" })
    if ($result.Count -ne 1) { Stop-Lab "Salida SQL inesperada para $name" }
    $expected = "$name|t|$slug|f|0|t"
    if ($result[0] -ne $expected) {
        Write-Host ('5AB AUDITORIA inesperada: ' + $result[0])
        Stop-Lab "Aislamiento SELECT o permisos NO aprobados para $name"
    }
    Write-Host ("5AB OK: " + $name + " ve SOLO " + $slug + "; GUC ajena no suplanta tenant")
}

try {
    Write-Host '5AB [1/5] Comprobar rama, archivo y clúster independiente'
    if ((Get-Location).Path -ne $root) { Stop-Lab 'Directorio distinto al proyecto' }
    if (-not (Test-Path -LiteralPath $psql -PathType Leaf) -or
        -not (Test-Path -LiteralPath $script -PathType Leaf) -or
        -not (Test-Path -LiteralPath (Join-Path $data 'PG_VERSION'))) {
        Stop-Lab 'Falta PostgreSQL 18 o alguno de los archivos del laboratorio'
    }
    if ((git branch --show-current).Trim() -ne 'feat/saas-multitenant-parcial2') {
        Stop-Lab 'Rama git incorrecta'
    }
    if (git status --porcelain) { Stop-Lab 'Cambios locales pendientes; no actualizar ni ejecutar' }
    [Environment]::SetEnvironmentVariable('PGSSLMODE', 'disable', 'Process')
    [Environment]::SetEnvironmentVariable('PGCONNECT_TIMEOUT', '8', 'Process')
    [Environment]::SetEnvironmentVariable('PGOPTIONS', $null, 'Process')

    $superPassword = Read-Host 'Clave del postgres NUEVO laboratorio (55432)' -AsSecureString
    Set-Password $superPassword
    Remove-Variable superPassword
    $preflight = @'
SELECT current_database() || '|' || inet_server_port()::text || '|' ||
 lower(rtrim(replace(current_setting('data_directory'), chr(92), '/'), '/')) || '|' ||
 current_setting('server_version_num')::int / 10000;
'@
    $identity = Get-One 'postgres' $preflight
    $expected = "fashionstore_rls_lab_5u|55432|c:/si2_parcial1/fashionstore-rls-lab-5v-data|18"
    if ($identity -ne $expected) { Stop-Lab 'Identidad de laboratorio no coincide: no aplicar SQL' }

    Write-Host '5AB [2/5] Crear identidades SIN PASSWORD y policies SELECT en una transaccion'
    & $psql -X -q -w -v 'ON_ERROR_STOP=1' -h 127.0.0.1 -p 55432 -U postgres -d $db -f $script
    if ($LASTEXITCODE -ne 0) {
        Stop-Lab 'SQL rechazado/ROLLBACK; no ejecutar segunda vez sin analizar resultado'
    }

    Write-Host '5AB [3/5] Asignar claves distintas a A y B, con psql interactivo'
    Write-Host '5AB: NO compartir estas contrasenas ni ponerlas en comandos'
    & $psql -X -q -w -v 'ON_ERROR_STOP=1' -h 127.0.0.1 -p 55432 -U postgres -d $db -c '\password fashionstore_5ab_a'
    if ($LASTEXITCODE -ne 0) { Stop-Lab 'No se pudo establecer clave A; NO repetir instalacion' }
    & $psql -X -q -w -v 'ON_ERROR_STOP=1' -h 127.0.0.1 -p 55432 -U postgres -d $db -c '\password fashionstore_5ab_b'
    if ($LASTEXITCODE -ne 0) { Stop-Lab 'No se pudo establecer clave B; NO repetir instalacion' }

    $idA = Get-One 'postgres' "SELECT id::text FROM public.empresa WHERE slug='empresa-lab-a'"
    $idB = Get-One 'postgres' "SELECT id::text FROM public.empresa WHERE slug='empresa-lab-b'"
    if ($idA -notmatch '^[0-9]+$' -or $idB -notmatch '^[0-9]+$' -or $idA -eq $idB) {
        Stop-Lab 'Identidades de empresas ficticias invalidas'
    }

    Write-Host '5AB [4/5] Probar con autentificacion REAL las empresas A y B'
    [Environment]::SetEnvironmentVariable('PGPASSWORD', $null, 'Process')
    Check-Role $roleA 'empresa-lab-a' $idB
    Check-Role $roleB 'empresa-lab-b' $idA

    Write-Host '5AB [5/5] Resultado'
    Write-Host '5AB OK: 2 ROLES LOGIN REALES NOBYPASSRLS; 2 TENANTS FICTICIOS'
    Write-Host '5AB OK: RLS SELECT EFECTIVO; GUC ALTERADA NO ABRE DATOS AJENOS'
    Write-Host '5AB OK: SIN LECTURA DE HASH, SIN EXECUTE DE SP LEGACY'
    Write-Host '5AB NO CERTIFICA TODAVIA A01-A14: carritos, reservas y concurrencia pendientes'
} finally {
    [Environment]::SetEnvironmentVariable('PGPASSWORD', $oldPassword, 'Process')
    [Environment]::SetEnvironmentVariable('PGOPTIONS', $oldOpts, 'Process')
    [Environment]::SetEnvironmentVariable('PGSSLMODE', $oldSsl, 'Process')
    [Environment]::SetEnvironmentVariable('PGCONNECT_TIMEOUT', $oldTimeout, 'Process')
}
