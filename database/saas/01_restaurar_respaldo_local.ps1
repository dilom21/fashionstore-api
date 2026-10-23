# FashionStore - Bloque 2 / Paso 1: prueba de restauracion LOCAL.
# PostgreSQL 18 en Windows. No conecta a Supabase y NO borra bases existentes.
# Solo acepta 127.0.0.1: puerto local y crea una BD NUEVA con nombre unico.
param(
    [Parameter(Mandatory = $true)]
    [string]$DumpPath,

    [ValidateRange(1, 65535)]
    [int]$LocalPort = 5432
)

$ErrorActionPreference = 'Stop'
$hostLocal = '127.0.0.1'
$usuarioLocal = 'postgres'

# Detectar el PostgreSQL cliente que ya utiliza el equipo.
$pgBin18 = 'C:\Program Files\PostgreSQL\18\bin'
if ((-not (Get-Command 'pg_restore' -ErrorAction SilentlyContinue)) -and (Test-Path -LiteralPath $pgBin18)) {
    $env:Path = "$pgBin18;$env:Path"
}
foreach ($herramienta in @('pg_restore', 'psql', 'createdb', 'pg_isready')) {
    if (-not (Get-Command $herramienta -ErrorAction SilentlyContinue)) {
        throw "No se encuentra $herramienta. Revisa C:\Program Files\PostgreSQL\18\bin."
    }
}

$archivo = Get-Item -LiteralPath $DumpPath -ErrorAction Stop
if ($archivo.Extension -ne '.dump' -or $archivo.Length -lt 1024) {
    throw 'Selecciona el .dump del Paso 2 (archivo valido, mayor a 1 KB).'
}
$checkPath = Join-Path $archivo.DirectoryName ("$($archivo.BaseName)_sha256.txt")
if (Test-Path -LiteralPath $checkPath) {
    $checkText = Get-Content -LiteralPath $checkPath -Raw
    if ($checkText -notmatch 'SHA256:\s*([a-fA-F0-9]{64})') {
        throw 'El archivo de checksum no contiene un SHA256 reconocible.'
    }
    $esperado = $Matches[1]
    $real = (Get-FileHash -LiteralPath $archivo.FullName -Algorithm SHA256).Hash
    if ($real -ne $esperado) {
        throw 'El checksum NO coincide. No restaurar un archivo posiblemente alterado.'
    }
    Write-Host 'Integridad del archivo: SHA256 correcto.' -ForegroundColor Green
} else {
    Write-Warning 'No se encontro el archivo SHA256 correspondiente; revisa el respaldo.'
}

Write-Host ''
Write-Host 'Destino fijo: PostgreSQL LOCAL 127.0.0.1' -ForegroundColor Cyan
Write-Host "Puerto local: $LocalPort"
Write-Host "Backup origen: $($archivo.Name)"
Write-Host 'No utiliza el host ni la contrasena de Supabase.'
Write-Host ''

& pg_isready --host=$hostLocal --port=$LocalPort
if ($LASTEXITCODE -ne 0) {
    throw 'PostgreSQL LOCAL no esta iniciado en 127.0.0.1. Verifica Get-Service *postgresql* y el puerto.'
}

$clave = Read-Host 'Contrasena LOCAL del usuario postgres (NO Supabase)' -AsSecureString
$handle = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($clave)
try {
    $env:PGPASSWORD = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($handle)
} finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($handle)
}
$env:PGSSLMODE = 'disable'

try {
    # Comprobar credenciales y que el servidor sea localhost antes de crear nada.
    $verificacion = & psql --host=$hostLocal --port=$LocalPort --username=$usuarioLocal --dbname=postgres --no-password --no-align --tuples-only --command="SELECT current_database()"
    if ($LASTEXITCODE -ne 0 -or ($verificacion | Out-String).Trim() -ne 'postgres') {
        throw 'No se pudo validar la conexion al PostgreSQL LOCAL. Revisa usuario/clave.'
    }

    # Siempre una base nueva, sin eliminar ni reutilizar otras bases.
    $nombre = 'fashionstore_restore_' + (Get-Date -Format 'yyyyMMdd_HHmmss') + '_' + (Get-Random -Minimum 100 -Maximum 999)
    if ($nombre -notmatch '^fashionstore_restore_[0-9]{8}_[0-9]{6}_[0-9]{3}$') {
        throw 'Nombre de base de pruebas invalido.'
    }
    Write-Host "Creando base de prueba NUEVA: $nombre" -ForegroundColor Cyan
    & createdb --host=$hostLocal --port=$LocalPort --username=$usuarioLocal --no-password --template=template0 --encoding=UTF8 $nombre
    if ($LASTEXITCODE -ne 0) {
        throw 'createdb fallo. No se modifico ninguna base preexistente.'
    }

    # PostgreSQL 18 crea el esquema public automaticamente en template0.
    # El dump de Supabase contiene CREATE SCHEMA public; de lo contrario
    # pg_restore fallaria con 'schema public already exists'.
    # Eliminamos SOLO ese esquema en la base que acabamos de crear, sin CASCADE.
    # Si NO esta vacio, DROP SCHEMA fallara y detendremos el proceso.
    $baseActual = & psql --host=$hostLocal --port=$LocalPort --username=$usuarioLocal --dbname=$nombre --no-password --no-align --tuples-only --set=ON_ERROR_STOP=1 --command="SELECT current_database()"
    if ($LASTEXITCODE -ne 0 -or ($baseActual | Out-String).Trim() -ne $nombre) {
        throw "No se pudo verificar la base nueva $nombre. No se eliminara ningun esquema."
    }
    Write-Host 'Preparando esquema public VACIO en la base nueva (sin CASCADE)...'
    & psql --host=$hostLocal --port=$LocalPort --username=$usuarioLocal --dbname=$nombre --no-password --set=ON_ERROR_STOP=1 --command="DROP SCHEMA public;"
    if ($LASTEXITCODE -ne 0) {
        throw "No se pudo quitar el esquema public VACIO de $nombre. Restauracion cancelada de forma segura."
    }

    Write-Host 'Restaurando dump en transaccion unica (un error revierte la restauracion)...'
    & pg_restore --host=$hostLocal --port=$LocalPort --username=$usuarioLocal --dbname=$nombre --no-password --no-owner --no-privileges --single-transaction --exit-on-error $archivo.FullName
    if ($LASTEXITCODE -ne 0) {
        throw "pg_restore fallo. La base de prueba $nombre sigue aislada; revisa el error antes de intentar otra vez."
    }

    $sql = @'
SELECT json_build_object(
    'tablas', (SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relkind='r'),
    'vistas', (SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relkind='v'),
    'usuarios', (SELECT count(*) FROM public.usuario),
    'clientes', (SELECT count(*) FROM public.cliente),
    'sucursales', (SELECT count(*) FROM public.sucursal),
    'productos', (SELECT count(*) FROM public.producto),
    'variantes', (SELECT count(*) FROM public.variante_producto),
    'inventarios', (SELECT count(*) FROM public.inventario),
    'reservas', (SELECT count(*) FROM public.reserva),
    'ventas', (SELECT count(*) FROM public.venta),
    'pagos', (SELECT count(*) FROM public.pago)
)::text;
'@
    $salida = & psql --host=$hostLocal --port=$LocalPort --username=$usuarioLocal --dbname=$nombre --no-password --no-align --tuples-only --set=ON_ERROR_STOP=1 --command=$sql
    if ($LASTEXITCODE -ne 0 -or -not $salida) {
        throw 'No fue posible consultar los registros restaurados.'
    }
    $estadisticas = (($salida | Out-String).Trim() | ConvertFrom-Json)
    if ([int]$estadisticas.tablas -ne 41) {
        throw "Se esperaban 41 tablas, pero se obtuvieron $($estadisticas.tablas)."
    }

    Write-Host ''
    Write-Host 'RESTAURACION LOCAL COMPLETADA' -ForegroundColor Green
    Write-Host "Base de prueba: $nombre"
    Write-Host "Servidor: $hostLocal`:$LocalPort"
    Write-Host ("Datos verificados: " + ($estadisticas | ConvertTo-Json -Compress))
    Write-Host 'La base local se conserva para las pruebas posteriores.'
    Write-Host 'NO subas el dump a GitHub ni actives SAAS_AUTH_ENABLED.'
} finally {
    Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue
    Remove-Item Env:PGSSLMODE -ErrorAction SilentlyContinue
}
