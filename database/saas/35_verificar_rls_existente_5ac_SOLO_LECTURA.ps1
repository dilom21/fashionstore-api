#Requires -Version 5.1
# FashionStore B3/5AC: diagnostico de RLS YA INSTALADO, solo lectura.
# Nunca vuelve a ejecutar 32_instalar...sql ni 33_aplicar...ps1.
# Solo 127.0.0.1:55432 / fashionstore_rls_lab_5u, con sesiones SQL REALES A/B.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$root = 'C:\SI2_Parcial1\fashionstore-api'
$psql = 'C:\Program Files\PostgreSQL\18\bin\psql.exe'
$db = 'fashionstore_rls_lab_5u'
$data = 'c:/si2_parcial1/fashionstore-rls-lab-5v-data'
$roleA = 'fashionstore_5ab_a'
$roleB = 'fashionstore_5ab_b'
$previousPassword = [Environment]::GetEnvironmentVariable('PGPASSWORD','Process')
$previousOptions = [Environment]::GetEnvironmentVariable('PGOPTIONS','Process')
$previousSsl = [Environment]::GetEnvironmentVariable('PGSSLMODE','Process')
$previousTimeout = [Environment]::GetEnvironmentVariable('PGCONNECT_TIMEOUT','Process')

function Block([string] $message) { throw ('5AC BLOQUEADO: ' + $message) }
function Set-SecurePassword([Security.SecureString] $secret) {
    $p = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secret)
    try {
        [Environment]::SetEnvironmentVariable('PGPASSWORD',
            [Runtime.InteropServices.Marshal]::PtrToStringBSTR($p), 'Process')
    } finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($p)
    }
}
function Select-One([string] $role,[string] $query) {
    $lines = @(& $psql -X -q -A -t -w -v 'ON_ERROR_STOP=1' -h 127.0.0.1 -p 55432 -U $role -d $db -c $query)
    if ($LASTEXITCODE -ne 0 -or $lines.Count -ne 1) {
        Block 'Fallo la lectura del preflight PostgreSQL 55432'
    }
    return ([string]$lines[0]).Trim()
}
function Check-Role([string] $role,[string] $ownSlug,[string] $otherSlug,[string] $otherId) {
    Write-Host "5AC SESION SQL REAL: $role (solicita su contrasena)"
    [Environment]::SetEnvironmentVariable('PGPASSWORD', $null, 'Process')
    $query = @"
SET app.empresa_id = '$otherId';
SET app.usuario_id = '999999';
SELECT session_user || '|' ||
       current_user || '|' ||
       current_database() || '|' ||
       host(inet_server_addr()) || '|' ||
       inet_server_port()::text || '|' ||
       (current_setting('server_version_num')::int / 10000)::text || '|' ||
       row_security_active('public.empresa'::regclass)::text || '|' ||
       coalesce((SELECT string_agg(slug, ',' ORDER BY slug) FROM public.empresa),'') || '|' ||
       (SELECT count(*) FROM public.empresa WHERE slug='$otherSlug')::text || '|' ||
       has_column_privilege(session_user,'public.usuario','password_hash','SELECT')::text || '|' ||
       (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public'
          AND p.proname IN ('sp_cancelar_reserva','sp_crear_reserva',
                           'sp_crear_reserva_desde_carrito','sp_finalizar_reserva_sin_compra')
          AND has_function_privilege(session_user,p.oid,'EXECUTE'))::text || '|' ||
       (row_security_active('public.carrito'::regclass) AND
        row_security_active('public.inventario'::regclass) AND
        row_security_active('public.reserva'::regclass) AND
        row_security_active('public.bitacora'::regclass))::text || '|' ||
       (NOT has_table_privilege(session_user,'public.reserva','INSERT') AND
        NOT has_table_privilege(session_user,'public.inventario','UPDATE') AND
        NOT has_table_privilege(session_user,'public.carrito','INSERT'))::text;
"@
    # -W: pedir la clave A/B interactivamente; no está escrita en código/argumentos.
    $rows = @(& $psql -X -q -A -t -W -v 'ON_ERROR_STOP=1' -h 127.0.0.1 -p 55432 -U $role -d $db -c $query)
    if ($LASTEXITCODE -ne 0) { Block "No pudo autenticar o consultar $role" }
    $found = @($rows | Where-Object { $_.StartsWith("$role|", [StringComparison]::Ordinal) })
    if ($found.Count -ne 1) { Block "Respuesta de autenticacion inesperada de $role" }
    # Cast boolean::text PostgreSQL produce true/false, no t/f del psql nativo.
    $expected = "$role|$role|$db|127.0.0.1|55432|18|true|$ownSlug|0|false|0|true|true"
    if ($found[0] -cne $expected) {
        Write-Host "5AC RESPUESTA NO ESPERADA: $($found[0])"
        Block "Seguridad de $role no cumple todas las condiciones"
    }
    Write-Host "5AC OK: $role solo ve $ownSlug, aun con app.empresa_id=$otherId"
    Write-Host "5AC OK: RLS activa, sin hash, sin SP legacy y sin permisos INSERT/UPDATE"
}
try {
    Write-Host '5AC [1/3] Verificacion de entorno SOLO LECTURA'
    if ((Get-Location).Path -ne $root) { Block 'Ubicacion inesperada del proyecto' }
    if (-not (Test-Path -LiteralPath $psql -PathType Leaf)) { Block 'Falta psql.exe PostgreSQL 18' }
    if ((git branch --show-current).Trim() -ne 'feat/saas-multitenant-parcial2') {
        Block 'Rama Git incorrecta'
    }
    if (git status --porcelain) { Block 'Existen cambios locales pendientes' }
    [Environment]::SetEnvironmentVariable('PGSSLMODE', 'disable', 'Process')
    [Environment]::SetEnvironmentVariable('PGCONNECT_TIMEOUT', '8', 'Process')
    [Environment]::SetEnvironmentVariable('PGOPTIONS', '-c default_transaction_read_only=on', 'Process')
    $superSecret = Read-Host 'Clave postgres SOLO del laboratorio 55432' -AsSecureString
    Set-SecurePassword $superSecret
    Remove-Variable superSecret
    $identity = Select-One 'postgres' @'
SELECT current_database() || '|' || host(inet_server_addr()) || '|' ||
       inet_server_port()::text || '|' ||
       lower(rtrim(replace(current_setting('data_directory'),chr(92),'/'),'/')) || '|' ||
       (current_setting('server_version_num')::int / 10000)::text || '|' ||
       (SELECT count(*) FROM pg_policies WHERE schemaname='public')::text || '|' ||
       (SELECT count(*) FROM pg_roles WHERE rolname IN
          ('fashionstore_5ab_a','fashionstore_5ab_b') AND
          rolcanlogin AND NOT rolsuper AND NOT rolbypassrls AND NOT rolinherit)::text || '|' ||
       (SELECT count(*) FROM public.empresa WHERE slug IN
          ('empresa-lab-a','empresa-lab-b'))::text;
'@
    $expectedIdentity = "$db|127.0.0.1|55432|$data|18|35|2|2"
    if ($identity -cne $expectedIdentity) {
        Block 'El laboratorio, roles, empresas o numero de policies no coinciden'
    }
    $ids = Select-One 'postgres' @'
SELECT (SELECT id::text FROM public.empresa WHERE slug='empresa-lab-a') ||
       '|' ||
       (SELECT id::text FROM public.empresa WHERE slug='empresa-lab-b');
'@
    if ($ids -notmatch '^([1-9][0-9]*)\|([1-9][0-9]*)$') {
        Block 'IDs ficticios A/B ausentes o invalidos'
    }
    $idA = $Matches[1]
    $idB = $Matches[2]
    if ($idA -eq $idB) { Block 'IDs A/B identicos' }
    [Environment]::SetEnvironmentVariable('PGPASSWORD', $null, 'Process')
    Write-Host '5AC [2/3] Comprobar aislamiento bajo cada LOGIN SQL real'
    Check-Role $roleA 'empresa-lab-a' 'empresa-lab-b' $idB
    Check-Role $roleB 'empresa-lab-b' 'empresa-lab-a' $idA
    Write-Host '5AC [3/3] RESULTADO: dos LOGIN SQL y RLS de lectura verificados'
    Write-Host '5AC OK: verificaciones sin DDL/DML ni modificaciones persistentes'
    Write-Host '5AC PENDIENTE: A01-A14 con datos empresariales ficticios y concurrencia'
} finally {
    [Environment]::SetEnvironmentVariable('PGPASSWORD', $previousPassword, 'Process')
    [Environment]::SetEnvironmentVariable('PGOPTIONS', $previousOptions, 'Process')
    [Environment]::SetEnvironmentVariable('PGSSLMODE', $previousSsl, 'Process')
    [Environment]::SetEnvironmentVariable('PGCONNECT_TIMEOUT', $previousTimeout, 'Process')
}
