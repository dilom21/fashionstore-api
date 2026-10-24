#Requires -Version 5.1
<#
 FashionStore / B3 5Z: CREACION AUTORIZADA de clúster LOCAL nuevo.
 Alcance EXCLUSIVO: PG18, 127.0.0.1:55432, PGDATA nuevo fijado.
 NO crea bases adicionales, usuarios/roles, tablas, policies ni tenants.
 NO cambia el clúster 5432, Supabase, ni las ramas git.
#>
$ErrorActionPreference = 'Stop'
$bin = 'C:\Program Files\PostgreSQL\18\bin'
$data = 'C:\SI2_Parcial1\fashionstore-rls-lab-5v-data'
$parent = 'C:\SI2_Parcial1'
$sourceDb = 'fashionstore_saas_reservas_sandbox_5h'
$port = 55432
$initdb = Join-Path $bin 'initdb.exe'
$pgctl = Join-Path $bin 'pg_ctl.exe'
$psql = Join-Path $bin 'psql.exe'
$serverLog = Join-Path $data 'servidor_laboratorio.log'

function Fail([string] $message) { throw ('5Z BLOQUEADO: ' + $message) }
function NoReparseAncestor([string] $path) {
    $item = Get-Item -LiteralPath $path -Force -ErrorAction Stop
    while ($null -ne $item) {
        if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            Fail 'Se detecto un enlace/junction en un directorio ancestro'
        }
        $item = $item.Parent
    }
}
function NormalizePath([string] $value) {
    return [IO.Path]::GetFullPath($value).TrimEnd([char]'\')
}
function SameOrInside([string] $a, [string] $b) {
    return $a.Equals($b, [StringComparison]::OrdinalIgnoreCase) -or
        $a.StartsWith(($b + '\'), [StringComparison]::OrdinalIgnoreCase) -or
        $b.StartsWith(($a + '\'), [StringComparison]::OrdinalIgnoreCase)
}
function PsqlQuery([string] $database, [int] $portNumber, [string] $sql) {
    $lines = @(& $psql -X -q -A -t -W -v 'ON_ERROR_STOP=1' -h '127.0.0.1' -p "$portNumber" -U 'postgres' -d $database -c $sql)
    if ($LASTEXITCODE -ne 0) { Fail "Fallo la consulta de verificacion a puerto $portNumber" }
    $value = @($lines | Where-Object { $_ -match '^[0-9]+\|' })
    if ($value.Count -ne 1) { Fail "Respuesta inesperada en puerto $portNumber" }
    return [string]$value[0]
}

Write-Host '5Z INICIO - CREACION DE INSTANCIA INDEPENDIENTE AUTORIZADA'
Write-Host '5Z [1/7] Comprobacion de herramientas y destino'
foreach ($exe in @($initdb, $pgctl, $psql)) {
    if (-not (Test-Path -LiteralPath $exe -PathType Leaf)) { Fail 'Falta un binario PostgreSQL 18 esperado' }
}
$v = & $initdb --version
if ($LASTEXITCODE -ne 0 -or $v -notmatch 'PostgreSQL\) 18\.') { Fail 'initdb no es PostgreSQL 18' }
if (-not (Test-Path -LiteralPath $parent -PathType Container)) { Fail 'No existe directorio padre autorizado' }
if (Test-Path -LiteralPath $data) { Fail 'PGDATA destino YA existe: NO reutilizar, no borrar' }
NoReparseAncestor $parent
if ((NormalizePath $data) -ne (NormalizePath 'C:\SI2_Parcial1\fashionstore-rls-lab-5v-data')) {
    Fail 'Ruta de laboratorio alterada'
}

Write-Host '5Z [2/7] Comprobacion de puerto no ocupado'
if (Get-Command Get-NetTCPConnection -ErrorAction SilentlyContinue) {
    $listeners = @(Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue)
    if ($listeners.Count -gt 0) { Fail 'Puerto 55432 ya esta escuchando' }
}
$probe = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, $port)
try { $probe.Start() } catch { Fail 'Puerto 55432 no pudo reservarse temporalmente' } finally { $probe.Stop() }

$previousOpts = [Environment]::GetEnvironmentVariable('PGOPTIONS', 'Process')
$previousSsl = [Environment]::GetEnvironmentVariable('PGSSLMODE', 'Process')
$previousTimeout = [Environment]::GetEnvironmentVariable('PGCONNECT_TIMEOUT', 'Process')
try {
    [Environment]::SetEnvironmentVariable('PGOPTIONS', '-c default_transaction_read_only=on', 'Process')
    [Environment]::SetEnvironmentVariable('PGSSLMODE', 'disable', 'Process')
    [Environment]::SetEnvironmentVariable('PGCONNECT_TIMEOUT', '8', 'Process')

    Write-Host '5Z [3/7] Lectura minima del cluster 5432 (solicita clave PostgreSQL LOCAL original)'
    $sqlSource = "SELECT system_identifier::text || '|' || current_setting('data_directory') || '|' || current_setting('server_version_num') || '|' || current_database() || '|' || inet_server_port()::text || '|' || session_user FROM pg_control_system()"
    $original = (PsqlQuery $sourceDb 5432 $sqlSource) -split '\|'
    if ($original.Count -ne 6 -or $original[0] -notmatch '^[0-9]+$' -or
        $original[2] -notmatch '^18[0-9]{4}$' -or $original[3] -ne $sourceDb -or
        $original[4] -ne '5432' -or $original[5] -ne 'postgres') {
        Fail 'No coincide la identidad/version/base del cluster original'
    }
    $sourcePath = NormalizePath $original[1]
    $targetPath = NormalizePath $data
    if (SameOrInside $targetPath $sourcePath) { Fail 'Origen y destino se solapan' }
    Write-Host '5Z ORIGEN VERIFICADO: no se imprime identificador ni PGDATA privado'

    Write-Host '5Z [4/7] initdb en directorio NUEVO. Elegir nueva clave SOLO del laboratorio'
    & $initdb '-D' $data '-U' 'postgres' '-E' 'UTF8' '--auth=scram-sha-256' '--pwprompt'
    if ($LASTEXITCODE -ne 0) { Fail 'initdb fallo; NO borrar ni reutilizar directorio sin inspeccion' }
    if (-not (Test-Path -LiteralPath (Join-Path $data 'PG_VERSION'))) { Fail 'initdb no genero PG_VERSION' }

    Write-Host '5Z [5/7] Restriccion de config SOLO del laboratorio'
    @(
        '# FashionStore: clúster desechable, acceso exclusivo loopback IPv4'
        'host all all 127.0.0.1/32 scram-sha-256'
        'host replication all 127.0.0.1/32 scram-sha-256'
    ) | Set-Content -LiteralPath (Join-Path $data 'pg_hba.conf') -Encoding Ascii
    @(
        ''
        "# 5Z: ajustes del laboratorio aislado, NO del servicio original"
        "listen_addresses = '127.0.0.1'"
        'port = 55432'
        "password_encryption = 'scram-sha-256'"
    ) | Add-Content -LiteralPath (Join-Path $data 'postgresql.conf') -Encoding Ascii

    Write-Host '5Z [6/7] Arranque con pg_ctl, SIN registrar servicio Windows'
    & $pgctl '-D' $data '-l' $serverLog '-w' '-t' '60' 'start'
    if ($LASTEXITCODE -ne 0) { Fail 'No arranco el laboratorio; revisar solo su log (no eliminar)' }

    Write-Host '5Z [7/7] Identidad y configuracion del laboratorio (solicita su NUEVA clave)'
    $sqlTarget = "SELECT system_identifier::text || '|' || current_setting('data_directory') || '|' || inet_server_port()::text || '|' || current_setting('listen_addresses') || '|' || (SELECT count(*) FROM pg_hba_file_rules WHERE error IS NOT NULL)::text FROM pg_control_system()"
    $target = (PsqlQuery 'postgres' $port $sqlTarget) -split '\|'
    if ($target.Count -ne 5 -or $target[0] -notmatch '^[0-9]+$' -or
        $target[0] -eq $original[0] -or
        (NormalizePath $target[1]) -ne $targetPath -or
        $target[2] -ne '55432' -or $target[3] -ne '127.0.0.1' -or $target[4] -ne '0') {
        Fail 'No se certifico independencia/configuracion: laboratorio requiere revision'
    }
    Write-Host '5Z OK: SEGUNDO CLUSTER POSTGRESQL 18 INDEPENDIENTE, RUTA/ID DIFERENTES'
    Write-Host '5Z OK: SOLO 127.0.0.1:55432, SCRAM-SHA-256, SIN SERVICIO WINDOWS'
    Write-Host '5Z OK: CLUSTER ORIGINAL 5432 SIN CAMBIOS, SIN DDL/ROLES/TABLAS'
    Write-Host '5Z PENDIENTE: ACL/RLS y 14 PRUEBAS A/B REALES NO EJECUTADAS'
} finally {
    [Environment]::SetEnvironmentVariable('PGOPTIONS', $previousOpts, 'Process')
    [Environment]::SetEnvironmentVariable('PGSSLMODE', $previousSsl, 'Process')
    [Environment]::SetEnvironmentVariable('PGCONNECT_TIMEOUT', $previousTimeout, 'Process')
}
