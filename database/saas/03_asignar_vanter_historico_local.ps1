# FASHIONSTORE / BLOQUE 2 / PASO 4
# SOLO RESTAURACION LOCAL del primer parcial, PostgreSQL 18 en Windows.
# Esta herramienta NO utiliza conexiones remotas ni elimina bases de datos.
$ErrorActionPreference = 'Stop'

$servidor = '127.0.0.1'
$puerto = 5432
$usuario = 'postgres'
$base = 'fashionstore_restore_20261023_193554_594'
$psql = 'C:\Program Files\PostgreSQL\18\bin\psql.exe'
$migracion = Join-Path $PSScriptRoot '..\..\supabase\migrations\20261023000200_vanter_tenancy_historica.sql'
if (-not (Test-Path -LiteralPath $psql -PathType Leaf)) {
    throw "psql.exe no encontrado: $psql"
}
if (-not (Test-Path -LiteralPath $migracion -PathType Leaf)) {
    throw "Migracion no encontrada: $migracion. Ejecuta git pull."
}
$migracion = (Resolve-Path -LiteralPath $migracion).Path

$originalPassword = [Environment]::GetEnvironmentVariable('PGPASSWORD','Process')
$originalSSL = [Environment]::GetEnvironmentVariable('PGSSLMODE','Process')
$originalEncoding = [Environment]::GetEnvironmentVariable('PGCLIENTENCODING','Process')
$clave = Read-Host 'Contrasena del PostgreSQL LOCAL (no Supabase)' -AsSecureString
$puntero = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($clave)
try {
    $env:PGPASSWORD = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($puntero)
} finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($puntero)
}
$env:PGSSLMODE = 'disable'
$env:PGCLIENTENCODING = 'UTF8'

try {
    function ConsultaJson {
        param([Parameter(Mandatory=$true)][string]$SQL)
        $salida = & $psql --host=$servidor --port=$puerto --username=$usuario --dbname=$base --no-password --no-psqlrc --no-align --tuples-only --set=ON_ERROR_STOP=1 --command=$SQL
        if ($LASTEXITCODE -ne 0 -or -not $salida) {
            throw 'Fallo una consulta de verificacion en PostgreSQL LOCAL.'
        }
        return (($salida | Out-String).Trim() | ConvertFrom-Json)
    }

    $antesSQL = @'
SELECT json_build_object(
  'base', current_database(),
  'host', host(inet_server_addr()),
  'tablas', (SELECT COUNT(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relkind='r'),
  'empresas', (SELECT COUNT(*) FROM public.empresa),
  'planes', (SELECT COUNT(*) FROM public.plan_saas),
  'suscripciones', (SELECT COUNT(*) FROM public.suscripcion),
  'membresias', (SELECT COUNT(*) FROM public.usuario_empresa),
  'configuraciones', (SELECT COUNT(*) FROM public.empresa_configuracion),
  'columnas_empresa_id_historicas', (
    SELECT COUNT(*) FROM information_schema.columns
    WHERE table_schema='public' AND column_name='empresa_id'
      AND table_name NOT IN ('empresa','plan_saas','suscripcion','usuario_empresa','empresa_configuracion')
  ),
  'usuarios', (SELECT COUNT(*) FROM public.usuario),
  'clientes', (SELECT COUNT(*) FROM public.cliente),
  'empleados', (SELECT COUNT(*) FROM public.empleado),
  'sucursales', (SELECT COUNT(*) FROM public.sucursal),
  'productos', (SELECT COUNT(*) FROM public.producto),
  'variantes', (SELECT COUNT(*) FROM public.variante_producto),
  'inventarios', (SELECT COUNT(*) FROM public.inventario),
  'reservas', (SELECT COUNT(*) FROM public.reserva),
  'ventas', (SELECT COUNT(*) FROM public.venta),
  'pagos', (SELECT COUNT(*) FROM public.pago),
  'bitacora', (SELECT COUNT(*) FROM public.bitacora),
  'triggers', (SELECT COUNT(*) FROM pg_trigger tg
     JOIN pg_class c ON c.oid=tg.tgrelid
     JOIN pg_namespace n ON n.oid=c.relnamespace
     WHERE n.nspname='public' AND NOT tg.tgisinternal)
)::text;
'@

    $antes = ConsultaJson -SQL $antesSQL
    if ($antes.base -cne $base -or $antes.host -cne $servidor) {
        $detalle = "Destino inesperado. Base: '{0}', host: '{1}'. Operacion abortada." -f $antes.base, $antes.host
        throw $detalle
    }
    if ([int]$antes.tablas -ne 46 -or
        [int]$antes.columnas_empresa_id_historicas -ne 0 -or
        [int]$antes.empresas -ne 0 -or
        [int]$antes.planes -ne 0 -or
        [int]$antes.suscripciones -ne 0 -or
        [int]$antes.membresias -ne 0 -or
        [int]$antes.configuraciones -ne 0 -or
        [int]$antes.triggers -ne 27) {
        throw 'Preverificacion fallida: se esperaban 46 tablas, cinco tablas SaaS vacias, 0 columnas historicas de tenant y 27 triggers.'
    }
    Write-Host ''
    Write-Host 'PREVERIFICACION OK' -ForegroundColor Green
    Write-Host "Host: $servidor  Puerto: $puerto  Base: $base"
    Write-Host ('Estado ANTES: ' + ($antes | ConvertTo-Json -Compress))
    Write-Host "SQL versionado: $migracion"
    Write-Host 'Se crearan solamente Vanter Men, sus 9 membresias y los dos planes.'
    Write-Host 'Se agregara empresa_id a 31 tablas historicas SIN UPDATE de filas.'
    Write-Host 'Esta base de pruebas NO es Supabase.'
    Write-Host ''
    $confirmacion = Read-Host 'Para aplicar exclusivamente en la base local escribe ASIGNAR'
    if ($confirmacion -cne 'ASIGNAR') {
        throw 'Accion cancelada; no se ejecuto la migracion.'
    }

    & $psql --host=$servidor --port=$puerto --username=$usuario --dbname=$base --no-password --no-psqlrc --set=ON_ERROR_STOP=1 --file=$migracion
    if ($LASTEXITCODE -ne 0) {
        throw 'Fallo la migracion. Una transaccion fallida se revierte. No avanzar.'
    }

    $despuesSQL = @'
SELECT json_build_object(
  'base', current_database(),
  'host', host(inet_server_addr()),
  'tablas', (SELECT COUNT(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relkind='r'),
  'empresas', (SELECT COUNT(*) FROM public.empresa),
  'planes', (SELECT COUNT(*) FROM public.plan_saas),
  'suscripciones', (SELECT COUNT(*) FROM public.suscripcion),
  'membresias', (SELECT COUNT(*) FROM public.usuario_empresa),
  'configuraciones', (SELECT COUNT(*) FROM public.empresa_configuracion),
  'columnas_empresa_id_historicas', (
    SELECT COUNT(*) FROM information_schema.columns
    WHERE table_schema='public' AND column_name='empresa_id'
      AND table_name NOT IN ('empresa','plan_saas','suscripcion','usuario_empresa','empresa_configuracion')
  ),
  'historicas_not_null', (
    SELECT COUNT(*) FROM information_schema.columns
    WHERE table_schema='public' AND column_name='empresa_id' AND is_nullable='NO'
      AND table_name NOT IN ('empresa','plan_saas','suscripcion','usuario_empresa','empresa_configuracion')
  ),
  'fk_empresa_historicas', (
    SELECT COUNT(*) FROM pg_constraint k
    JOIN pg_class c ON c.oid=k.conrelid
    JOIN pg_namespace n ON n.oid=c.relnamespace
    WHERE n.nspname='public' AND k.contype='f'
      AND k.confrelid='public.empresa'::regclass
      AND c.relname NOT IN ('empresa','plan_saas','suscripcion','usuario_empresa','empresa_configuracion')
  ),
  'empresa_vanter_id', (SELECT id FROM public.empresa WHERE slug='vanter-men'),
  'vanter_profesional', (SELECT COUNT(*) FROM public.suscripcion s
    JOIN public.empresa e ON e.id=s.empresa_id
    JOIN public.plan_saas p ON p.id=s.plan_saas_id
    WHERE e.slug='vanter-men' AND p.codigo='PROFESIONAL' AND s.estado='ACTIVA'),
  'sucursales_vanter', (SELECT COUNT(*) FROM public.sucursal su
    JOIN public.empresa e ON e.id=su.empresa_id WHERE e.slug='vanter-men'),
  'ventas_vanter', (SELECT COUNT(*) FROM public.venta v
    JOIN public.empresa e ON e.id=v.empresa_id WHERE e.slug='vanter-men'),
  'pagos_vanter', (SELECT COUNT(*) FROM public.pago pa
    JOIN public.empresa e ON e.id=pa.empresa_id WHERE e.slug='vanter-men'),
  'usuarios', (SELECT COUNT(*) FROM public.usuario),
  'clientes', (SELECT COUNT(*) FROM public.cliente),
  'empleados', (SELECT COUNT(*) FROM public.empleado),
  'sucursales', (SELECT COUNT(*) FROM public.sucursal),
  'productos', (SELECT COUNT(*) FROM public.producto),
  'variantes', (SELECT COUNT(*) FROM public.variante_producto),
  'inventarios', (SELECT COUNT(*) FROM public.inventario),
  'reservas', (SELECT COUNT(*) FROM public.reserva),
  'ventas', (SELECT COUNT(*) FROM public.venta),
  'pagos', (SELECT COUNT(*) FROM public.pago),
  'bitacora', (SELECT COUNT(*) FROM public.bitacora),
  'triggers', (SELECT COUNT(*) FROM pg_trigger tg
     JOIN pg_class c ON c.oid=tg.tgrelid
     JOIN pg_namespace n ON n.oid=c.relnamespace
     WHERE n.nspname='public' AND NOT tg.tgisinternal)
)::text;
'@
    $despues = ConsultaJson -SQL $despuesSQL
    if ($despues.base -cne $base -or $despues.host -cne $servidor) {
        throw 'La postverificacion no corresponde al servidor/base local esperados.'
    }
    $errores = @()
    $esperados = @{
        tablas=46; empresas=1; planes=2; suscripciones=1; membresias=9;
        configuraciones=1; columnas_empresa_id_historicas=31;
        historicas_not_null=30; fk_empresa_historicas=31;
        vanter_profesional=1; sucursales_vanter=4;
        ventas_vanter=40; pagos_vanter=30; triggers=27
    }
    foreach ($campo in $esperados.Keys) {
        if ([int]$despues.$campo -ne [int]$esperados[$campo]) {
            $errores += "$campo esperado=$($esperados[$campo]) real=$($despues.$campo)"
        }
    }
    foreach ($campo in @('usuarios','clientes','empleados','sucursales',
                        'productos','variantes','inventarios','reservas','ventas',
                        'pagos','bitacora')) {
        if ($antes.$campo -ne $despues.$campo) {
            $errores += "Cambio de conteo historico $campo"
        }
    }
    Write-Host ''
    Write-Host ('Estado DESPUES: ' + ($despues | ConvertTo-Json -Compress))
    if ($errores.Count -gt 0) {
        foreach ($err in $errores) { Write-Host ("ERROR: "+$err) -ForegroundColor Red }
        throw 'Postverificacion fallida. No seguir con la migracion.'
    }

    Write-Host ''
    Write-Host 'VANTER MEN ASIGNADO CORRECTAMENTE EN POSTGRESQL LOCAL' -ForegroundColor Green
    Write-Host '31 tablas historicas, 9 usuarios y 4 sucursales asociados a Vanter Men.'
    Write-Host '27 triggers preservados. Resto de datos historicos sin perdida de filas.'
    Write-Host 'NO se modifico Supabase. Mantener SAAS_AUTH_ENABLED=false.'
} finally {
    if ($null -eq $originalPassword) {
        Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue
    } else { $env:PGPASSWORD=$originalPassword }
    if ($null -eq $originalSSL) {
        Remove-Item Env:PGSSLMODE -ErrorAction SilentlyContinue
    } else { $env:PGSSLMODE=$originalSSL }
    if ($null -eq $originalEncoding) {
        Remove-Item Env:PGCLIENTENCODING -ErrorAction SilentlyContinue
    } else { $env:PGCLIENTENCODING=$originalEncoding }
}
