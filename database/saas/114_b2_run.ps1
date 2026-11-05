<#
.SYNOPSIS
    5BW - Ejecucion B2 supervisada por el propietario (un solo archivo).

.DESCRIPTION
    Orquesta la secuencia autorizada del documento 104:
      00 guardas -> 01 puerta PRE-B2 -> 02 UNICA extraccion public ->
      03 cifrado FSB2AEAD v2 + verificacion de etiqueta -> 04 restauracion
      PG17 local aislado -> 05 retorno, auditoria comparada y limpieza.

    Modos:
      Validar   (por defecto) Solo comprobaciones estaticas y locales.
                NO abre conexion a Supabase y NO genera respaldo.
      Simulacro Ensayo completo de la maquinaria con datos SINTETICOS en un
                cluster local desechable. No toca Supabase.
      Real      Ejecucion B2 autorizada. Exige -ConfirmarB2 y una confirmacion
                escrita en consola. Una sola extraccion, sin reintentos.

    Garantias:
      * try/finally: ante cualquier fallo se borran dump plano y temporales,
        se limpia PGPASSWORD y se detiene el cluster local.
      * El paquete cifrado .fsb2 NUNCA se borra automaticamente.
      * Se comprueba el codigo de salida de TODOS los comandos criticos.
      * La ventana del 104 se revalida inmediatamente antes del unico pg_dump.
      * La contrasena del origen la pide pg_dump (-W) y la frase la pide el
        instrumento 110 (getpass): nunca pasan por variables, evidencias,
        historial ni mensajes.

.PARAMETER Modo
    Validar | Simulacro | Real.

.PARAMETER Autorizacion
    Ruta del documento 104 firmado.

.PARAMETER OrigenHost
    Host directo del proyecto Supabase autorizado.

.PARAMETER PuertoRestore
    Puerto loopback del cluster desechable (por defecto 55452).

.PARAMETER PuertoSimulacro
    Puerto loopback del ensayo sintetico (por defecto 55451).

.PARAMETER ConfirmarB2
    Obligatorio en -Modo Real.

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File database\saas\114_b2_run.ps1 -Modo Validar

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File database\saas\114_b2_run.ps1 -Modo Simulacro
#>

[CmdletBinding()]
param(
    [ValidateSet('Validar', 'Simulacro', 'Real')]
    [string]$Modo = 'Validar',

    [string]$Autorizacion = 'database\saas\104_solicitud_autorizacion_datos_5bs.md',

    [string]$OrigenHost = 'db.zyitwlxfkphthlvlmuej.supabase.co',
    [int]$OrigenPuerto = 5432,
    [int]$PuertoRestore = 55452,
    [int]$PuertoSimulacro = 55451,

    [switch]$ConfirmarB2,
    [switch]$MantenerSimulacro,

    # Solo para pruebas del propio script en -Modo Simulacro.
    [ValidateSet('ninguno', 'tras-dump', 'tras-cifrado', 'tras-restore', 'tras-retorno')]
    [string]$InyectarFallo = 'ninguno'
)

$ErrorActionPreference = 'Stop'

# ---------------------------------------------------------------------------
# Rutas y constantes
# ---------------------------------------------------------------------------
$RepoRoot   = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$Evidencias = Join-Path $RepoRoot 'database\saas\evidencias'
$Tool110    = Join-Path $RepoRoot 'database\saas\110_cifrado_autenticado_b2_5bu.py'
$PgBin      = Join-Path $env:LOCALAPPDATA 'Temp\opencode\fs_pg17_5bp\pg17\pgsql\bin'
$WorkRoot   = Join-Path $env:LOCALAPPDATA 'Temp\opencode'

if (-not [IO.Path]::IsPathRooted($Autorizacion)) {
    $Autorizacion = Join-Path $RepoRoot $Autorizacion
}

$VentanaIni = [DateTimeOffset]::Parse('2026-10-31T15:55:00-04:00')
$VentanaFin = [DateTimeOffset]::Parse('2026-11-01T23:59:00-04:00')

$script:Fallos = New-Object System.Collections.ArrayList
$script:Avisos = New-Object System.Collections.ArrayList
$script:Sello  = Get-Date -Format 'yyyyMMdd_HHmmss'

# Estado para el bloque finally
$Dump        = $null
$Paquete     = $null
$PgData      = $null
$PwFile      = $null
$PassSim     = $null
$ClusterOn   = $false
$Descifrado  = $false
$B2Pw        = $null

# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------
function Write-Paso {
    param([string]$Texto)
    Write-Host ''
    Write-Host "== $Texto" -ForegroundColor Cyan
}

function Add-Fallo {
    param([string]$Texto)
    [void]$script:Fallos.Add($Texto)
    Write-Host "   FALLO: $Texto" -ForegroundColor Red
}

function Add-Aviso {
    param([string]$Texto)
    [void]$script:Avisos.Add($Texto)
    Write-Host "   AVISO: $Texto" -ForegroundColor Yellow
}

function Get-AhoraB2 {
    return [DateTimeOffset]::UtcNow.ToOffset([TimeSpan]::FromHours(-4))
}

function Assert-Ventana {
    param([string]$Momento)
    $ahora = Get-AhoraB2
    if ($ahora -lt $VentanaIni -or $ahora -gt $VentanaFin) {
        Add-Fallo ("fuera de ventana en {0}: {1:yyyy-MM-dd HH:mm} -04:00" -f $Momento, $ahora)
        return $false
    }
    Write-Host ("   ventana OK en {0}: {1:yyyy-MM-dd HH:mm} -04:00" -f $Momento, $ahora)
    return $true
}

function Test-PuertoLibre {
    param([int]$Puerto, [int]$EsperaMs = 400)
    $c = New-Object Net.Sockets.TcpClient
    try {
        return -not $c.ConnectAsync('127.0.0.1', $Puerto).Wait($EsperaMs)
    } catch {
        return $true
    } finally {
        $c.Close()
    }
}

function Invoke-Exe {
    <#
      Ejecuta un binario con ErrorActionPreference='Continue' durante la llamada.
      En PowerShell 5.1 cualquier linea de stderr de un nativo se convierte en
      ErrorRecord; con EAP='Stop' abortaria el flujo aunque el rc sea correcto.
      El veredicto se decide SIEMPRE por el codigo de salida devuelto.
    #>
    param(
        [string]$Exe,
        [string[]]$Argv,
        [string]$Log,
        [switch]$Visible
    )
    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        if ($Log) { $out = @(& $Exe @Argv 2> $Log) }
        else      { $out = @(& $Exe @Argv) }
        $rc = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $prev
    }
    if ($Visible) { foreach ($l in $out) { Write-Host $l } }
    return $rc
}

function Invoke-Critico {
    <# Ejecuta un comando nativo y falla cerrado si el codigo de salida no es 0. #>
    param(
        [string]$Exe,
        [string[]]$Argv,
        [string]$Nombre,
        [switch]$Silencioso
    )
    if ($Silencioso) {
        $tmp = [IO.Path]::GetTempFileName()
        try {
            $rc = Invoke-Exe -Exe $Exe -Argv $Argv -Log $tmp
            if ($rc -ne 0) {
                Get-Content $tmp -ErrorAction SilentlyContinue | Select-Object -Last 15 |
                    ForEach-Object { Write-Host "      $_" -ForegroundColor DarkGray }
            }
        } finally {
            Remove-Item $tmp -Force -ErrorAction SilentlyContinue
        }
    } else {
        $rc = Invoke-Exe -Exe $Exe -Argv $Argv
    }
    if ($rc -ne 0) {
        throw "Comando critico fallo: $Nombre (rc=$rc)"
    }
    Write-Host "   OK  $Nombre" -ForegroundColor Green
    return $rc
}

function Invoke-Sql {
    <# Ejecuta SQL con salida separada por '|' y devuelve lineas. Falla cerrado. #>
    param(
        [string]$Sql,
        [string]$Base = 'postgres',
        [string[]]$PgArgs,
        [string]$Nombre = 'psql'
    )
    $out = & (Join-Path $PgBin 'psql.exe') @PgArgs -A -t -F '|' -d $Base -v ON_ERROR_STOP=1 -c $Sql
    $rc = $LASTEXITCODE
    if ($rc -ne 0) { throw "Comando critico fallo: $Nombre (rc=$rc)" }
    Write-Host "   OK  $Nombre" -ForegroundColor Green
    return @($out)
}

function Get-TocCounts {
    <# Clasifica las entradas del listado TOC de pg_restore. #>
    param([string[]]$Lineas)
    $c = [ordered]@{
        tablas = 0; datos = 0; secuencias = 0; vistas = 0;
        funciones = 0; triggers = 0; indices = 0; policies = 0;
        constraint = 0; fk = 0; acl = 0; acl_default = 0
    }
    foreach ($l in $Lineas) {
        if ($l -match '\bTABLE DATA public ')          { $c.datos++ }
        elseif ($l -match '\bTABLE public ')           { $c.tablas++ }
        elseif ($l -match '\bSEQUENCE (OWNED BY|SET) public ') { }
        elseif ($l -match '\bSEQUENCE public ')        { $c.secuencias++ }
        elseif ($l -match '\bMATERIALIZED VIEW public '){ $c.vistas++ }
        elseif ($l -match '\bVIEW public ')            { $c.vistas++ }
        elseif ($l -match '\b(FUNCTION|PROCEDURE|AGGREGATE) public ') { $c.funciones++ }
        elseif ($l -match '\bTRIGGER public ')         { $c.triggers++ }
        elseif ($l -match '\bINDEX public ')           { $c.indices++ }
        elseif ($l -match '\bPOLICY public ')          { $c.policies++ }
        elseif ($l -match '\bFK CONSTRAINT public ')   { $c.fk++ }
        elseif ($l -match '\bCONSTRAINT public ')      { $c.constraint++ }
        elseif ($l -match '\bDEFAULT ACL\b.* public ') { $c.acl_default++ }
        elseif ($l -match '\bACL\b.* public ')         { $c.acl++ }
    }
    return $c
}

function Get-InventarioRestaurado {
    <# Conteos del catalogo de la base restaurada. #>
    param([string[]]$PgArgs, [string]$Base)
    $sql = @'
SELECT 'tablas', count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
  WHERE n.nspname='public' AND c.relkind IN ('r','p')
UNION ALL SELECT 'secuencias', count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
  WHERE n.nspname='public' AND c.relkind='S'
UNION ALL SELECT 'vistas', count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
  WHERE n.nspname='public' AND c.relkind IN ('v','m')
UNION ALL SELECT 'funciones', count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
  WHERE n.nspname='public' AND p.prokind IN ('f','p','a')
UNION ALL SELECT 'triggers', count(*) FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid
  JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND NOT t.tgisinternal
UNION ALL SELECT 'indices', count(*) FROM pg_indexes WHERE schemaname='public'
UNION ALL SELECT 'policies', count(*) FROM pg_policies WHERE schemaname='public'
UNION ALL SELECT 'fk', count(*) FROM pg_constraint c JOIN pg_class r ON r.oid=c.conrelid
  JOIN pg_namespace n ON n.oid=r.relnamespace WHERE n.nspname='public' AND c.contype='f'
UNION ALL SELECT 'constraint', count(*) FROM pg_constraint c JOIN pg_class r ON r.oid=c.conrelid
  JOIN pg_namespace n ON n.oid=r.relnamespace WHERE n.nspname='public' AND c.contype IN ('p','u','x')
UNION ALL SELECT 'acl_objetos', count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
  WHERE n.nspname='public' AND c.relacl IS NOT NULL;
'@
    $lineas = Invoke-Sql -Sql $sql -Base $Base -PgArgs $PgArgs -Nombre "inventario restaurado ($Base)"
    $inv = [ordered]@{}
    foreach ($l in $lineas) {
        if ($l -match '^(?<k>[a-z_]+)\|(?<v>\d+)$') { $inv[$Matches['k']] = [int]$Matches['v'] }
    }
    return $inv
}

function Get-Filas {
    param([string[]]$PgArgs, [string]$Base, [string]$Nombre)
    $sql = @'
SELECT c.relname,
       (xpath('/row/c/text()',
              query_to_xml(format('SELECT count(*) AS c FROM %I.%I', n.nspname, c.relname),
                           false, true, '')))[1]::text::bigint
FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
WHERE n.nspname='public' AND c.relkind IN ('r','p') AND c.relpersistence='p'
ORDER BY 1;
'@
    $lineas = Invoke-Sql -Sql $sql -Base $Base -PgArgs $PgArgs -Nombre $Nombre
    return @($lineas | Where-Object { $_ -match '\|' })
}

function Compare-Inventarios {
    <#
      Compara dump vs restaurado categoria por categoria y devuelve filas de
      comparacion. 'comparable' = $false significa que la diferencia es
      esperada por construccion y NO se declara igualdad.
    #>
    param($Toc, $Inv)

    $filas = New-Object System.Collections.ArrayList

    $defs = @(
        @{ n='tablas';     d=$Toc.tablas;      r=$Inv.tablas;      c=$true;  nota='tablas y particionadas' }
        @{ n='datos';      d=$Toc.datos;       r=$Inv.tablas;      c=$true;  nota='seccion TABLE DATA por tabla' }
        @{ n='secuencias'; d=$Toc.secuencias;  r=$Inv.secuencias;  c=$true;  nota='relkind=S' }
        @{ n='vistas';     d=$Toc.vistas;      r=$Inv.vistas;      c=$true;  nota='vistas + materializadas' }
        @{ n='funciones';  d=$Toc.funciones;   r=$Inv.funciones;   c=$true;  nota='f/p/a en pg_proc' }
        @{ n='triggers';   d=$Toc.triggers;    r=$Inv.triggers;    c=$true;  nota='no internos' }
        @{ n='fk';         d=$Toc.fk;          r=$Inv.fk;          c=$true;  nota='FK CONSTRAINT' }
        @{ n='constraint'; d=$Toc.constraint;  r=$Inv.constraint;  c=$true;  nota='PK/UNIQUE/EXCLUDE; los CHECK pueden ir embebidos en CREATE TABLE y no aparecer en el TOC' }
        @{ n='indices';    d=$Toc.indices;     r=$Inv.indices;     c=$false; nota='los indices de PK/UNIQUE se crean implicita y no aparecen como INDEX en el TOC; no comparables 1:1' }
        @{ n='policies';   d=$Toc.policies;    r=$Inv.policies;    c=$true;  nota='RLS' }
        @{ n='acl';        d=($Toc.acl + $Toc.acl_default); r=$Inv.acl_objetos; c=$false; nota='permisos: el TOC emite GRANT/REVOKE por objeto; el cluster local agrega privilegios por defecto del propietario, por lo que la igualdad numerica no es exigible' }
    )

    foreach ($d in $defs) {
        $estado = 'NO_COMPARABLE'
        if ($d.c) {
            if ($d.d -eq $d.r) { $estado = 'IGUAL' } else { $estado = 'DIFERENTE' }
        }
        [void]$filas.Add([pscustomobject]@{
            categoria  = $d.n
            dump       = $d.d
            restaurado = $d.r
            comparable = [bool]$d.c
            estado     = $estado
            nota       = $d.nota
        })
    }
    return $filas
}

function Write-TablaComparacion {
    param($Filas)
    Write-Host ''
    Write-Host ("   {0,-12} {1,8} {2,12} {3,-14} {4}" -f 'categoria', 'dump', 'restaurado', 'estado', 'nota') -ForegroundColor DarkGray
    foreach ($f in $Filas) {
        $color = 'Gray'
        if ($f.estado -eq 'IGUAL')         { $color = 'Green' }
        elseif ($f.estado -eq 'DIFERENTE') { $color = 'Red' }
        elseif ($f.estado -eq 'NO_COMPARABLE') { $color = 'Yellow' }
        Write-Host ("   {0,-12} {1,8} {2,12} {3,-14} {4}" -f $f.categoria, $f.dump, $f.restaurado, $f.estado, $f.nota) -ForegroundColor $color
    }
}

function New-Cluster {
    param([string]$Ruta, [int]$Puerto, [string]$DirDestino)
    $pw = $null
    $pf = Join-Path $DirDestino '.initdb_pw'
    $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
    $buf = New-Object byte[] 24
    $rng.GetBytes($buf); $rng.Dispose()
    $pw = [Convert]::ToBase64String($buf)
    Set-Content -Path $pf -Value $pw -NoNewline -Encoding ascii
    # (F) y no (R): el archivo debe poder borrarse en la limpieza.
    icacls "$pf" /inheritance:r /grant:r "$($env:USERNAME):F" | Out-Null
    try {
        $null = Invoke-Critico -Exe (Join-Path $PgBin 'initdb.exe') `
            -Argv @('-D', $Ruta, '-U', 'postgres', "--pwfile=$pf",
                    '--auth-local=trust', '--auth-host=scram-sha-256', '--encoding=UTF8') `
            -Nombre 'initdb (cluster desechable)' -Silencioso
    } finally {
        if (Test-Path $pf) {
            icacls "$pf" /reset *> $null
            Remove-Item $pf -Force -ErrorAction SilentlyContinue
        }
    }

    # postgres.exe se lanza con Start-Process (sin -Wait) y sus flujos van a
    # archivo. Usar pg_ctl start desde -File deja a PowerShell esperando el
    # handle heredado del servidor; este arranque no bloquea.
    Write-Host "   arrancando cluster en 127.0.0.1:$Puerto ..."
    $argLine = "-D `"$Ruta`" -p $Puerto -c listen_addresses=127.0.0.1"
    $proc = Start-Process -FilePath (Join-Path $PgBin 'postgres.exe') -ArgumentList $argLine `
        -RedirectStandardOutput (Join-Path $DirDestino 'pg.log') `
        -RedirectStandardError (Join-Path $DirDestino 'pg.err.log') `
        -WindowStyle Hidden -PassThru
    if (-not $proc) { throw 'No se pudo lanzar postgres.exe.' }

    $prev = $env:PGPASSWORD
    $env:PGPASSWORD = $pw
    $listo = $false
    try {
        for ($i = 0; $i -lt 60; $i++) {
            & (Join-Path $PgBin 'psql.exe') -h 127.0.0.1 -p $Puerto -U postgres -d postgres `
                -w -c 'SELECT 1' *> $null
            if ($LASTEXITCODE -eq 0) { $listo = $true; break }
            if ($i -lt 3) { Write-Host "   esperando disponibilidad (intento $($i + 1)) ..." }
            Start-Sleep -Milliseconds 500
        }
    } finally {
        $env:PGPASSWORD = $prev
    }
    if (-not $listo) {
        throw "El cluster de $Ruta no acepto conexiones en 30 s. Revisa $DirDestino\pg.err.log"
    }
    Write-Host '   OK  servidor PostgreSQL 17 arriba' -ForegroundColor Green
    return $pw
}

function Stop-Cluster {
    param([string]$Ruta)
    if (-not $Ruta -or -not (Test-Path $Ruta)) { return $true }
    & (Join-Path $PgBin 'pg_ctl.exe') -D $Ruta status *> $null
    if ($LASTEXITCODE -ne 0) { return $true }
    # Sin redireccion: se espera el cierre real del servidor.
    & (Join-Path $PgBin 'pg_ctl.exe') -D $Ruta -m fast -w stop
    $rc = $LASTEXITCODE
    if ($rc -ne 0) { Add-Fallo "pg_ctl stop rc=$rc"; return $false }
    Write-Host '   OK  pg_ctl stop' -ForegroundColor Green
    return $true
}

# ---------------------------------------------------------------------------
# Preparacion comun
# ---------------------------------------------------------------------------
Write-Host ''
Write-Host "B2 SUPERVISADO - modo: $Modo" -ForegroundColor White
Write-Host "Repositorio: $RepoRoot"

$B2 = [ordered]@{
    etapa                  = '5BW'
    modo                   = $Modo
    sello                  = $script:Sello
    iniciado_local         = (Get-Date).ToString('o')
    autorizacion           = $Autorizacion
    origen_host            = $OrigenHost
    origen_puerto          = $OrigenPuerto
    origen_base            = 'postgres'
    origen_rol             = 'postgres'
    esquema                = 'public'
    ventana_autorizada     = '2026-10-31 15:55 -> 2026-11-01 23:59 America/La_Paz'
    puerto_restore         = $PuertoRestore
    supabase_modificado    = $false
    extracciones_intentadas = 0
}
if ($Modo -eq 'Simulacro') {
    # Nunca dejar rastro de contacto con el origen real en un ensayo.
    $B2.origen_host   = '127.0.0.1 (sintetico)'
    $B2.origen_puerto = $PuertoSimulacro
    $B2.origen_base   = 'fs_b2_sim (sintetica)'
    $B2.origen_rol    = 'postgres (cluster local desechable)'
}

try {
    if ($InyectarFallo -ne 'ninguno' -and $Modo -ne 'Simulacro') {
        throw 'InyectarFallo solo se admite en -Modo Simulacro.'
    }
    # -----------------------------------------------------------------
    # 00 - Guardas locales
    # -----------------------------------------------------------------
    Write-Paso '00 Guardas locales'

    if (-not (Test-Path $PgBin)) { throw "No existe la ruta de binarios 17.x: $PgBin" }
    $vDump = (& (Join-Path $PgBin 'pg_dump.exe') --version) -join ''
    $vRest = (& (Join-Path $PgBin 'pg_restore.exe') --version) -join ''
    if ($vDump -notmatch '17\.') { throw "pg_dump no es 17.x: $vDump" }
    if ($vRest -notmatch '17\.') { throw "pg_restore no es 17.x: $vRest" }
    Write-Host "   $vDump / $vRest" -ForegroundColor Green
    $B2.pg_dump    = $vDump
    $B2.pg_restore = $vRest

    $py = Get-Command python -ErrorAction SilentlyContinue
    if (-not $py) { throw 'No se encontro python en PATH.' }
    $pyVer = (& python -c "import cryptography,sys;print(sys.version.split()[0]+' cryptography '+cryptography.__version__)") -join ''
    if ($LASTEXITCODE -ne 0) { throw 'python no puede importar cryptography.' }
    Write-Host "   python $pyVer" -ForegroundColor Green
    $B2.python = $pyVer

    if (-not (Test-Path $Tool110))  { throw "No existe el instrumento: $Tool110" }
    if (-not (Test-Path $Autorizacion)) { throw "No existe la autorizacion: $Autorizacion" }
    if (-not (Test-Path $Evidencias)) { New-Item -ItemType Directory -Force -Path $Evidencias | Out-Null }

    Remove-Item Env:PGPASSWORD, Env:PGSSLMODE, Env:PGOPTIONS -ErrorAction SilentlyContinue

    $puertoUsar = if ($Modo -eq 'Simulacro') { $PuertoSimulacro } else { $PuertoRestore }
    if (-not (Test-PuertoLibre -Puerto $puertoUsar)) {
        throw "El puerto $puertoUsar esta en uso. Detente: no cambies de puerto sin decision."
    }
    Write-Host "   puerto $puertoUsar libre" -ForegroundColor Green
    $B2.puerto_usado = $puertoUsar

    $Destino = Join-Path $WorkRoot ("fs_b2_{0}_{1}" -f $Modo.ToLower(), $script:Sello)
    New-Item -ItemType Directory -Force -Path $Destino | Out-Null
    icacls "$Destino" /inheritance:r /grant:r "$($env:USERNAME):(OI)(CI)F" | Out-Null
    $B2.destino = $Destino
    Write-Host "   destino: $Destino" -ForegroundColor Green

    if (-not (Assert-Ventana -Momento 'inicio')) { throw 'Fuera de la ventana autorizada del 104.' }

    # -----------------------------------------------------------------
    # 01 - Puerta PRE-B2 (solo en Real; no abre conexion al origen)
    # -----------------------------------------------------------------
    if ($Modo -ne 'Simulacro') {
        Write-Paso '01 Puerta PRE-B2 (fail-closed; sin contacto al origen)'
        $prevEap = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        try {
            $salida = @(& python $Tool110 preflight `
                --origen-host $OrigenHost --origen-puerto $OrigenPuerto `
                --destino "$Destino" --puerto-restore $PuertoRestore `
                --autorizacion $Autorizacion)
            $rc = $LASTEXITCODE
        } finally {
            $ErrorActionPreference = $prevEap
        }
        $texto = ($salida -join "`n")
        Write-Host $texto
        if ($rc -ne 0 -or $texto -notmatch '"ok"\s*:\s*true') {
            throw 'La puerta PRE-B2 no abrio (ok!=true). No se contacta el origen.'
        }
        $B2.preflight_ok = $true
        Write-Host '   OK  preflight ok=true' -ForegroundColor Green
    } elseif ($Modo -eq 'Simulacro') {
        Write-Paso '01 Puerta PRE-B2 omitida (origen sintetico local; el gate exige origen remoto)'
        $B2.preflight_ok = $null
    }

    if ($Modo -eq 'Validar') {
        Write-Paso '01b Capacidades del instrumento AEAD (local)'
        & python $Tool110 estado
        if ($LASTEXITCODE -ne 0) { throw 'El instrumento 110 no responde a estado.' }
        $B2.tool_estado_ok = $true
    }

    # -----------------------------------------------------------------
    # 02 - Origen del dump
    # -----------------------------------------------------------------
    if ($Modo -ne 'Validar') {
    $Dump = Join-Path $Destino 'base.dump'
    $Paquete = Join-Path $Destino 'base.dump.fsb2'
    if (Test-Path $Dump) { throw "Ya existe $Dump. No se repite la extraccion." }
    if (Test-Path $Paquete) { throw "Ya existe $Paquete. No se sobrescribe." }

    if ($Modo -eq 'Real') {
        Write-Paso '02 UNICA extraccion de solo lectura (public)'
        if (-not $ConfirmarB2) { throw 'Modo Real exige -ConfirmarB2.' }
        $res = Read-Host 'Escribe AUTORIZO B2 para continuar con la extraccion unica'
        if ($res -ne 'AUTORIZO B2') { throw 'Confirmacion escrita incorrecta: no se extrae nada.' }

        # Revalidacion de ventana INMEDIATAMENTE antes del unico pg_dump
        if (-not (Assert-Ventana -Momento 'antes de pg_dump')) {
            throw 'La ventana expiro justo antes de pg_dump: extraccion NO ejecutada.'
        }

        # Candado anti-reintento: una extraccion B2 por autorizacion.
        $MarcaExtraccion = Join-Path $WorkRoot 'fs_b2_extraccion_intentada.json'
        if (Test-Path $MarcaExtraccion) {
            throw ("Ya consta una extraccion B2 previa ({0}). NO se repite sin nueva autorizacion firmada. " -f $MarcaExtraccion) +
                  'Borra ese archivo solo si el propietario emitio una autorizacion nueva.'
        }
        [ordered]@{
            sello      = $script:Sello
            iniciada   = (Get-Date).ToUniversalTime().ToString('o')
            host       = $OrigenHost
            puerto     = $OrigenPuerto
            esquema    = 'public'
            modo       = 'Real'
        } | ConvertTo-Json | Set-Content -Encoding utf8 $MarcaExtraccion
        $B2.marca_extraccion = $MarcaExtraccion

        $env:PGSSLMODE = 'require'
        $env:PGOPTIONS = '-c statement_timeout=0'
        $B2.extracciones_intentadas = 1
        $t0 = Get-Date
        # Sin captura ni redireccion: el prompt de contrasena debe ser visible
        # y el secreto no pasa por ninguna variable de PowerShell.
        $prevEap = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        try {
            & (Join-Path $PgBin 'pg_dump.exe') `
                --host $OrigenHost --port $OrigenPuerto --username postgres --dbname postgres `
                --schema=public --format=custom --compress=6 `
                --no-publications --no-subscriptions --no-tablespaces `
                --file "$Dump" --password
            $rcDump = $LASTEXITCODE
        } finally {
            $ErrorActionPreference = $prevEap
        }
        $t1 = Get-Date
        Remove-Item Env:PGSSLMODE, Env:PGOPTIONS -ErrorAction SilentlyContinue
        if ($rcDump -ne 0) {
            throw "pg_dump fallo (rc=$rcDump). NO se reintenta: requiere autorizacion nueva."
        }
        Write-Host "   OK  pg_dump ($([int]($t1 - $t0).TotalSeconds)s)" -ForegroundColor Green
        $B2.dump_seg = [math]::Round(($t1 - $t0).TotalSeconds, 1)

    } else {
        Write-Paso '02 Origen SINTETICO para simulacro (sin Supabase)'
        $PgData = Join-Path $Destino 'pgdata'
        $B2Pw = New-Cluster -Ruta $PgData -Puerto $PuertoSimulacro -DirDestino $Destino
        $ClusterOn = $true
        $env:PGPASSWORD = $B2Pw
        $P = @('-h', '127.0.0.1', '-p', "$PuertoSimulacro", '-U', 'postgres')

        # Base sintetica primero; el seed y el dump usan la MISMA base.
        $null = Invoke-Critico -Exe (Join-Path $PgBin 'createdb.exe') -Nombre 'createdb simulacro' -Silencioso `
            -Argv ($P + @('-T', 'template0', 'fs_b2_sim'))

        $sqlSeed = @'
CREATE ROLE fs_sim_rw NOLOGIN;
CREATE TABLE public.cliente (id serial PRIMARY KEY, nombre text NOT NULL, email text UNIQUE);
CREATE TABLE public.pedido (
  id bigserial PRIMARY KEY,
  cliente_id int NOT NULL REFERENCES public.cliente(id),
  total numeric(10,2) NOT NULL CHECK (total >= 0),
  creado timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_pedido_cliente ON public.pedido(cliente_id);
CREATE SEQUENCE public.folio_seq START 100;
CREATE FUNCTION public.fn_total(p int) RETURNS numeric LANGUAGE sql AS
  $$ SELECT COALESCE(SUM(total),0) FROM public.pedido WHERE cliente_id = p $$;
CREATE FUNCTION public.fn_touch() RETURNS trigger LANGUAGE plpgsql AS
  $$ BEGIN NEW.creado := now(); RETURN NEW; END $$;
CREATE TRIGGER trg_touch BEFORE UPDATE ON public.pedido
  FOR EACH ROW EXECUTE FUNCTION public.fn_touch();
CREATE VIEW public.v_pedido_cliente AS
  SELECT p.id, c.nombre, p.total FROM public.pedido p JOIN public.cliente c ON c.id = p.cliente_id;
ALTER TABLE public.pedido ENABLE ROW LEVEL SECURITY;
CREATE POLICY pol_pedido ON public.pedido FOR SELECT USING (true);
GRANT SELECT ON public.v_pedido_cliente TO fs_sim_rw;
INSERT INTO public.cliente(nombre,email) VALUES ('a','a@x.test'),('b','b@x.test');
INSERT INTO public.pedido(cliente_id,total) VALUES (1,10.50),(2,20.00),(1,5.25);
'@
        $null = Invoke-Critico -Exe (Join-Path $PgBin 'psql.exe') -Nombre 'seed sintetico' -Silencioso `
            -Argv ($P + @('-d', 'fs_b2_sim', '-v', 'ON_ERROR_STOP=1', '-c', $sqlSeed))

        $t0 = Get-Date
        $null = Invoke-Critico -Exe (Join-Path $PgBin 'pg_dump.exe') -Nombre 'pg_dump sintetico' -Silencioso `
            -Argv @('--host', '127.0.0.1', '--port', "$PuertoSimulacro", '--username', 'postgres',
                    '--dbname', 'fs_b2_sim', '--schema=public', '--format=custom', '--compress=6',
                    '--no-publications', '--no-subscriptions', '--no-tablespaces',
                    '--file', $Dump, '--no-password')
        $B2.dump_seg = [math]::Round(((Get-Date) - $t0).TotalSeconds, 1)
        $B2.extracciones_intentadas = 0   # sintetico, no cuenta como extraccion B2
    }

    if (-not (Test-Path $Dump)) { throw 'No se genero el dump.' }
    $B2.dump_bytes  = (Get-Item $Dump).Length
    $B2.dump_sha256 = (Get-FileHash $Dump -Algorithm SHA256).Hash
    Write-Host "   dump: $($B2.dump_bytes) bytes  sha256=$($B2.dump_sha256)" -ForegroundColor Green
    if ($InyectarFallo -eq 'tras-dump') { throw 'fallo inyectado: tras-dump' }

    # -----------------------------------------------------------------
    # 03 - Cifrado AEAD v2 + verificacion de etiqueta
    # -----------------------------------------------------------------
    Write-Paso '03 Cifrado FSB2AEAD v2 y verificacion de etiqueta'

    if ($Modo -eq 'Simulacro') {
        # Frase sintetica en archivo temporal restringido; se destruye en finally.
        $PassSim = Join-Path $Destino '.sim_pw'
        $rng2 = [Security.Cryptography.RandomNumberGenerator]::Create()
        $b2buf = New-Object byte[] 32
        $rng2.GetBytes($b2buf); $rng2.Dispose()
        Set-Content -Path $PassSim -Value ([Convert]::ToBase64String($b2buf)) -NoNewline -Encoding ascii
        icacls "$PassSim" /inheritance:r /grant:r "$($env:USERNAME):F" | Out-Null
        $cifrarArgs = @($Tool110, 'cifrar', $Dump, $Paquete, '--passfile', $PassSim,
                        '--meta', 'etapa=B2', '--meta', 'alcance=public', '--meta', 'modo=simulacro')
    } else {
        # Frase interactiva (getpass). No se pasa por argumento ni por variable.
        $cifrarArgs = @($Tool110, 'cifrar', $Dump, $Paquete,
                        '--meta', 'etapa=B2', '--meta', 'alcance=public',
                        '--meta', 'origen=supabase_fashionstore')
    }

    $rcCif = Invoke-Exe -Exe 'python' -Argv $cifrarArgs -Visible
    if ($rcCif -ne 0) { throw "Fallo el cifrado (rc=$rcCif)." }
    Write-Host '   OK  cifrado AEAD v2' -ForegroundColor Green

    if ($Modo -eq 'Simulacro') {
        $rcVer = Invoke-Exe -Exe 'python' -Argv @($Tool110, 'verificar', $Paquete, '--passfile', $PassSim) -Visible
    } else {
        $rcVer = Invoke-Exe -Exe 'python' -Argv @($Tool110, 'verificar', $Paquete) -Visible
    }
    if ($rcVer -ne 0) {
        throw 'Etiqueta AEAD no valida: no se descifra ni se restaura. El dump plano sigue disponible para recifrar.'
    }
    Write-Host '   OK  etiqueta AEAD verificada' -ForegroundColor Green

    $B2.pkg_bytes  = (Get-Item $Paquete).Length
    $B2.pkg_sha256 = (Get-FileHash $Paquete -Algorithm SHA256).Hash
    $B2.formato    = 'FSB2AEAD v2 / AES-256-GCM / PBKDF2-HMAC-SHA256 600000'
    $B2.tag_ok     = $true

    # Higiene: el texto plano no se necesita mas
    Remove-Item $Dump -Force
    if (Test-Path $Dump) { throw 'No se pudo borrar el dump plano tras el cifrado.' }
    $B2.plaintext_tras_cifrado = $true
    Write-Host '   OK  dump plano eliminado' -ForegroundColor Green
    if ($InyectarFallo -eq 'tras-cifrado') { throw 'fallo inyectado: tras-cifrado' }

    # -----------------------------------------------------------------
    # 04 - Restauracion en PG17 local aislado
    # -----------------------------------------------------------------
    Write-Paso '04 Restauracion en PostgreSQL 17 local aislado'

    if (-not $PgData) {
        $PgData = Join-Path $Destino 'pgdata'
        $B2Pw = New-Cluster -Ruta $PgData -Puerto $PuertoRestore -DirDestino $Destino
    }
    $ClusterOn = $true
    $env:PGPASSWORD = $B2Pw
    $P = @('-h', '127.0.0.1', '-p', "$($B2.puerto_usado)", '-U', 'postgres')

    # 4.1 roles locales que el dump puede referenciar (solo en este cluster)
    $sqlRoles = @'
DO $$
DECLARE r text;
BEGIN
  FOREACH r IN ARRAY ARRAY['anon','authenticated','service_role','authenticator',
        'supabase_admin','supabase_auth_admin','supabase_storage_admin',
        'supabase_replication_admin','supabase_read_only_user','dashboard_user',
        'pgbouncer','fs_saas_runtime','saas_admin_runtime'] LOOP
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r) THEN
      EXECUTE format('CREATE ROLE %I NOLOGIN', r);
    END IF;
  END LOOP;
END $$;
'@
    $null = Invoke-Sql -Sql $sqlRoles -Base 'postgres' -PgArgs $P -Nombre 'roles locales NOLOGIN'

    # 4.2 descifrado (solo publica el .dump tras validar el tag)
    if ($Modo -eq 'Simulacro') {
        $rcDes = Invoke-Exe -Exe 'python' -Argv @($Tool110, 'descifrar', $Paquete, $Dump, '--passfile', $PassSim) -Visible
    } else {
        $rcDes = Invoke-Exe -Exe 'python' -Argv @($Tool110, 'descifrar', $Paquete, $Dump) -Visible
    }
    if ($rcDes -ne 0) { throw "Descifrado fallido (rc=$rcDes): no se restaura nada." }
    $Descifrado = $true
    Write-Host '   OK  descifrado' -ForegroundColor Green

    # 4.3 TOC del dump: decide si el dump recrea el esquema public
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $tocLineas = @(& (Join-Path $PgBin 'pg_restore.exe') --no-password -l "$Dump")
        $rcToc = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $prevEap
    }
    if ($rcToc -ne 0) { throw "No se pudo listar el TOC del dump (rc=$rcToc)." }
    $tocLineas | Set-Content -Encoding utf8 (Join-Path $Destino 'toc.txt')
    $creaSchemaPublic = (@($tocLineas | Where-Object { $_ -match '\bSCHEMA - public ' }).Count -gt 0)
    $B2.dump_crea_schema_public = $creaSchemaPublic
    Write-Host "   TOC: $($tocLineas.Count) entradas  (dump crea schema public: $creaSchemaPublic)"

    # 4.4 base destino
    $null = Invoke-Critico -Exe (Join-Path $PgBin 'createdb.exe') -Nombre 'createdb fs_b2_restore' -Silencioso `
        -Argv ($P + @('-T', 'template0', 'fs_b2_restore'))
    if ($creaSchemaPublic) {
        # template0 ya trae public; el dump lo vuelve a crear y fallaria.
        $null = Invoke-Sql -Sql 'DROP SCHEMA public CASCADE' -Base 'fs_b2_restore' -PgArgs $P `
            -Nombre 'DROP SCHEMA public previo'
    }

    # 4.5 restauracion estricta
    $logRestore = Join-Path $Destino 'restore_principal.log'
    $B2.restore_rc = Invoke-Exe -Exe (Join-Path $PgBin 'pg_restore.exe') `
        -Argv ($P + @('-d', 'fs_b2_restore', '--exit-on-error', '--no-password', $Dump)) -Log $logRestore
    if ($B2.restore_rc -ne 0) {
        Get-Content $logRestore -Tail 30 | ForEach-Object { Write-Host "      $_" -ForegroundColor DarkGray }
        throw "pg_restore principal fallo (rc=$($B2.restore_rc))."
    }
    Write-Host '   OK  pg_restore principal' -ForegroundColor Green
    if ($InyectarFallo -eq 'tras-restore') { throw 'fallo inyectado: tras-restore' }

    # 4.6 inventarios y comparacion automatizada
    $toc = Get-TocCounts -Lineas $tocLineas
    $inv = Get-InventarioRestaurado -PgArgs $P -Base 'fs_b2_restore'

    $comparacion = Compare-Inventarios -Toc $toc -Inv $inv
    Write-TablaComparacion -Filas $comparacion

    $difComparables = @($comparacion | Where-Object { $_.comparable -and $_.estado -eq 'DIFERENTE' })
    $B2.comparacion = @($comparacion)
    $B2.diferencias_comparables = $difComparables.Count
    if ($difComparables.Count -gt 0) {
        foreach ($d in $difComparables) {
            Add-Fallo ("inventario {0}: dump={1} restaurado={2}" -f $d.categoria, $d.dump, $d.restaurado)
        }
    }

    # 4.6 filas agregadas y secuencias
    $filasRestore = Get-Filas -PgArgs $P -Base 'fs_b2_restore' -Nombre 'filas restauradas'
    $filasRestore | Set-Content -Encoding utf8 (Join-Path $Destino 'filas_restore.csv')
    $B2.tablas_con_filas = $filasRestore.Count
    $B2.total_filas = 0
    foreach ($f in $filasRestore) {
        $partes = $f -split '\|'
        if ($partes.Count -eq 2) { $B2.total_filas += [int64]$partes[1] }
    }
    Write-Host "   tablas con filas: $($B2.tablas_con_filas)  total filas: $($B2.total_filas)" -ForegroundColor Green

    $seq = Invoke-Sql -Sql "SELECT sequencename, last_value FROM pg_sequences WHERE schemaname='public' ORDER BY 1;" `
        -Base 'fs_b2_restore' -PgArgs $P -Nombre 'secuencias restauradas'
    $seq | Set-Content -Encoding utf8 (Join-Path $Destino 'secuencias_restore.csv')
    $B2.secuencias_reportadas = @($seq | Where-Object { $_ -match '\|' }).Count

    # -----------------------------------------------------------------
    # 05 - Retorno, limpieza y evidencia
    # -----------------------------------------------------------------
    Write-Paso '05 Retorno, limpieza y evidencia'

    $null = Invoke-Critico -Exe (Join-Path $PgBin 'createdb.exe') -Nombre 'createdb fs_b2_retorno' -Silencioso `
        -Argv ($P + @('-T', 'template0', 'fs_b2_retorno'))
    if ($creaSchemaPublic) {
        $null = Invoke-Sql -Sql 'DROP SCHEMA public CASCADE' -Base 'fs_b2_retorno' -PgArgs $P `
            -Nombre 'DROP SCHEMA public previo (retorno)'
    }

    $logRetorno = Join-Path $Destino 'restore_retorno.log'
    $B2.restore_retorno_rc = Invoke-Exe -Exe (Join-Path $PgBin 'pg_restore.exe') `
        -Argv ($P + @('-d', 'fs_b2_retorno', '--exit-on-error', '--no-password', $Dump)) -Log $logRetorno
    if ($B2.restore_retorno_rc -ne 0) {
        Get-Content $logRetorno -Tail 30 | ForEach-Object { Write-Host "      $_" -ForegroundColor DarkGray }
        throw "pg_restore de retorno fallo (rc=$($B2.restore_retorno_rc))."
    }
    Write-Host '   OK  pg_restore de retorno' -ForegroundColor Green
    if ($InyectarFallo -eq 'tras-retorno') { throw 'fallo inyectado: tras-retorno' }

    $filasRetorno = Get-Filas -PgArgs $P -Base 'fs_b2_retorno' -Nombre 'filas del retorno'
    $filasRetorno | Set-Content -Encoding utf8 (Join-Path $Destino 'filas_retorno.csv')
    $dif = Compare-Object -ReferenceObject $filasRestore -DifferenceObject $filasRetorno
    $B2.diferencias_retorno = @($dif).Count
    if ($B2.diferencias_retorno -gt 0) {
        Add-Fallo "retorno: $($B2.diferencias_retorno) diferencias de filas por tabla"
    } else {
        Write-Host '   OK  retorno: 0 diferencias de filas' -ForegroundColor Green
    }

    # 5.1 limpieza de bases
    $null = Invoke-Critico -Exe (Join-Path $PgBin 'psql.exe') -Nombre 'DROP DATABASE (limpieza)' -Silencioso `
        -Argv ($P + @('-d', 'postgres', '-v', 'ON_ERROR_STOP=1',
                      '-c', 'DROP DATABASE IF EXISTS fs_b2_retorno',
                      '-c', 'DROP DATABASE IF EXISTS fs_b2_restore'))

    # 5.2 detener cluster y borrar datadir
    $parado = Stop-Cluster -Ruta $PgData
    if ($parado) {
        $ClusterOn = $false
        Remove-Item $PgData -Recurse -Force -ErrorAction SilentlyContinue
        $B2.cluster_borrado = -not (Test-Path $PgData)
        if (-not $B2.cluster_borrado) { Add-Fallo 'no se pudo borrar el datadir del cluster desechable' }
    }

    # 5.3 borrar texto plano
    if (Test-Path $Dump) { Remove-Item $Dump -Force -ErrorAction SilentlyContinue }
    $B2.plaintext_borrado = -not (Test-Path $Dump)
    if (-not $B2.plaintext_borrado) { Add-Fallo 'quedo un dump plano sin borrar' }

    # 5.4 temporales sensibles
    foreach ($t in @($PassSim, (Join-Path $Destino '.initdb_pw'))) {
        if ($t -and (Test-Path $t)) {
            icacls "$t" /reset *> $null
            Remove-Item $t -Force -ErrorAction SilentlyContinue
        }
    }
    $B2.temporales_borrados = -not (($PassSim -and (Test-Path $PassSim)) -or (Test-Path (Join-Path $Destino '.initdb_pw')))

    Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue
    $B2.pgp_password_limpio = -not (Test-Path Env:PGPASSWORD)
    $B2Pw = $null

    $B2.puerto_liberado = Test-PuertoLibre -Puerto ($B2.puerto_usado)
    if (-not $B2.puerto_liberado) { Add-Fallo "el puerto $($B2.puerto_usado) sigue ocupado tras la limpieza" }

    $B2.paquete_conservado = Test-Path $Paquete
    $B2.paquete_ruta = $Paquete
    $B2.expiracion_utc = (Get-Date).ToUniversalTime().AddHours(24).ToString('o')
    }   # fin del pipeline de datos (no aplica en -Modo Validar)
}
catch {
    Add-Fallo $_.Exception.Message
    $B2.error = $_.Exception.Message
}
finally {
    # Limpieza incondicional de material sensible
    try {
        if ($Dump -and (Test-Path $Dump)) {
            Remove-Item $Dump -Force -ErrorAction SilentlyContinue
            if (Test-Path $Dump) { Add-Fallo 'finally: no se pudo borrar el dump plano' }
            else { Write-Host '   finally: dump plano eliminado' -ForegroundColor DarkGray }
        }
        foreach ($t in @($PassSim, $PwFile)) {
            if ($t -and (Test-Path $t)) {
                icacls "$t" /reset *> $null
                Remove-Item $t -Force -ErrorAction SilentlyContinue
            }
        }
        Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue
        Remove-Item Env:PGSSLMODE -ErrorAction SilentlyContinue
        Remove-Item Env:PGOPTIONS -ErrorAction SilentlyContinue

        if ($ClusterOn -and $PgData -and (Test-Path $PgData)) {
            $ok = Stop-Cluster -Ruta $PgData
            if ($ok -and -not $MantenerSimulacro) {
                Remove-Item $PgData -Recurse -Force -ErrorAction SilentlyContinue
                if (Test-Path $PgData) { Add-Fallo 'finally: no se pudo borrar el datadir' }
            }
        }

        # Higiene del ensayo: el destino sintetico no contiene datos reales.
        if ($Modo -eq 'Simulacro' -and -not $MantenerSimulacro -and
            $script:Fallos.Count -eq 0 -and $Destino -and (Test-Path $Destino)) {
            icacls "$Destino" /reset /T /C *> $null
            Remove-Item $Destino -Recurse -Force -ErrorAction SilentlyContinue
        }

        # Hechos de limpieza verificados SIEMPRE, tambien tras un fallo.
        if ($Dump)   { $B2.plaintext_borrado = -not (Test-Path $Dump) }
        if ($PgData) { $B2.cluster_borrado   = -not (Test-Path $PgData) }
        $tempVivos = @()
        foreach ($t in @($PassSim, $PwFile)) { if ($t -and (Test-Path $t)) { $tempVivos += $t } }
        if ($Destino -and (Test-Path (Join-Path $Destino '.initdb_pw'))) {
            $tempVivos += (Join-Path $Destino '.initdb_pw')
        }
        $B2.temporales_borrados = ($tempVivos.Count -eq 0)
        $B2.pgp_password_limpio = -not (Test-Path Env:PGPASSWORD)
        if ($Paquete) { $B2.paquete_conservado = Test-Path $Paquete }
        if ($B2.Contains('puerto_usado')) {
            $B2.puerto_liberado = Test-PuertoLibre -Puerto ($B2.puerto_usado)
        }
        $B2.expiracion_utc = (Get-Date).ToUniversalTime().AddHours(24).ToString('o')
    } catch {
        Add-Fallo ('finally: ' + $_.Exception.Message)
    }
}

# ---------------------------------------------------------------------------
# Veredicto y evidencia
# ---------------------------------------------------------------------------
$B2.fallos = @($script:Fallos)
$B2.avisos = @($script:Avisos)
$B2.finalizado_local = (Get-Date).ToString('o')

$veredicto = 'NO_EJECUTADO'
if ($Modo -eq 'Validar') {
    if ($script:Fallos.Count -eq 0) { $veredicto = 'VALIDACION_OK' } else { $veredicto = 'VALIDACION_FALLIDA' }
} elseif ($Modo -eq 'Simulacro') {
    if ($script:Fallos.Count -eq 0) { $veredicto = 'SIMULACRO_OK' } else { $veredicto = 'SIMULACRO_FALLIDO' }
} else {
    $restoreOk = ($B2.restore_rc -eq 0) -and ($B2.restore_retorno_rc -eq 0)
    $cifradoOk = ($B2.tag_ok -eq $true)
    $limpioOk  = ($B2.plaintext_borrado -eq $true) -and ($B2.temporales_borrados -eq $true) -and ($B2.pgp_password_limpio -eq $true)
    if ($script:Fallos.Count -eq 0 -and $restoreOk -and $cifradoOk -and $limpioOk -and $B2.diferencias_comparables -eq 0) {
        $veredicto = 'GO_B2_LOCAL'
    } elseif ($B2.extracciones_intentadas -gt 0 -and $B2.tag_ok -eq $true) {
        $veredicto = 'PARCIAL'
    } else {
        $veredicto = 'NO-GO'
    }
}
$B2.veredicto = $veredicto

$prefijo = 'EVIDENCIA_B2_SIM_5BW_'
if ($Modo -eq 'Real') { $prefijo = 'EVIDENCIA_B2_REAL_5BW_' }
$rutaEvidencia = Join-Path $Evidencias ($prefijo + $script:Sello + '.json')
$B2.ruta_evidencia = $rutaEvidencia

Write-Host ''
Write-Host "VEREDICTO: $veredicto" -ForegroundColor $(if ($veredicto -like '*OK*' -or $veredicto -eq 'GO_B2_LOCAL') { 'Green' } else { 'Red' })
if ($script:Fallos.Count -gt 0) {
    Write-Host 'Fallos:' -ForegroundColor Red
    $script:Fallos | ForEach-Object { Write-Host "  - $_" -ForegroundColor Red }
}

if ($Modo -ne 'Validar') {
    $B2 | ConvertTo-Json -Depth 6 | Set-Content -Encoding utf8 $rutaEvidencia
    Write-Host ''
    Write-Host "Evidencia: $rutaEvidencia"
    Write-Host 'Resumen copiable:'
    Write-Host '--------------------------------------------------------------'
    Write-Host ("modo={0} veredicto={1} sello={2}" -f $B2.modo, $B2.veredicto, $B2.sello)
    Write-Host ("pg_dump={0} | pg_restore={1}" -f $B2.pg_dump, $B2.pg_restore)
    Write-Host ("dump_bytes={0} dump_sha256={1}" -f $B2.dump_bytes, $B2.dump_sha256)
    Write-Host ("pkg_bytes={0} pkg_sha256={1}" -f $B2.pkg_bytes, $B2.pkg_sha256)
    Write-Host ("tag_ok={0} restore_rc={1} retorno_rc={2}" -f $B2.tag_ok, $B2.restore_rc, $B2.restore_retorno_rc)
    Write-Host ("tablas_con_filas={0} total_filas={1} dif_comparables={2} dif_retorno={3}" -f `
        $B2.tablas_con_filas, $B2.total_filas, $B2.diferencias_comparables, $B2.diferencias_retorno)
    Write-Host ("plaintext_borrado={0} cluster_borrado={1} temporales_borrados={2} puerto_liberado={3}" -f `
        $B2.plaintext_borrado, $B2.cluster_borrado, $B2.temporales_borrados, $B2.puerto_liberado)
    Write-Host ("paquete_conservado={0} expira_utc={1}" -f $B2.paquete_conservado, $B2.expiracion_utc)
    Write-Host '--------------------------------------------------------------'
}

if ($script:Fallos.Count -gt 0) { exit 1 } else { exit 0 }
