# FashionStore - BLOQUE 2 / PASO 3
# Aplica la migracion base SaaS SOLO a la BD local restaurada y validada.
# NO usa URL ni contrasena de Supabase. NO aplica migraciones remotas.
# Cada SELECT de verificacion se ejecuta contra 127.0.0.1 y el nombre fijo.
$ErrorActionPreference = 'Stop'

$servidor = '127.0.0.1'
$puerto = 5432
$usuario = 'postgres'
$base = 'fashionstore_restore_20261023_193554_594'
$pgBin = 'C:\Program Files\PostgreSQL\18\bin'
$psql = Join-Path $pgBin 'psql.exe'
$migracion = Join-Path $PSScriptRoot '..\..\supabase\migrations\20261023000100_saas_base_tables.sql'

if (-not (Test-Path -LiteralPath $psql -PathType Leaf)) {
    throw "No se encontro psql.exe en $psql"
}
if (-not (Test-Path -LiteralPath $migracion -PathType Leaf)) {
    throw "No se encontro la migracion versionada en $migracion. Ejecuta git pull."
}
$migracion = (Resolve-Path -LiteralPath $migracion).Path
if ($base -notmatch '^fashionstore_restore_[0-9]{8}_[0-9]{6}_[0-9]{3}$') {
    throw 'Destino no autorizado. Solo se permite la base local de restauracion.'
}

$originalPassword = [Environment]::GetEnvironmentVariable('PGPASSWORD', 'Process')
$originalSsl = [Environment]::GetEnvironmentVariable('PGSSLMODE', 'Process')
$passSecure = Read-Host 'Contrasena del PostgreSQL LOCAL (no Supabase)' -AsSecureString
$ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($passSecure)
try {
    $env:PGPASSWORD = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)
} finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr)
}
$env:PGSSLMODE = 'disable'

try {
    function ConsultarJson {
        param([Parameter(Mandatory = $true)][string]$Consulta)
        # Los parametros de conexion son constantes y apuntan al servidor local.
        $respuesta = & $psql --host=$servidor --port=$puerto --username=$usuario --dbname=$base --no-password --no-psqlrc --tuples-only --no-align --set=ON_ERROR_STOP=1 --command=$Consulta
        if ($LASTEXITCODE -ne 0 -or -not $respuesta) {
            throw 'No se pudo ejecutar la comprobacion SQL en la base LOCAL.'
        }
        return (($respuesta | Out-String).Trim() | ConvertFrom-Json)
    }

    $lecturaBase = @'
SELECT json_build_object(
  'base', current_database(),
  'servidor', host(inet_server_addr()),
  'tablas', (SELECT COUNT(*) FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public' AND c.relkind = 'r'),
  'saas', (SELECT COUNT(*) FROM information_schema.tables
    WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
      AND table_name IN ('empresa','plan_saas','suscripcion',
                         'usuario_empresa','empresa_configuracion')),
  'usuarios', (SELECT COUNT(*) FROM public.usuario),
  'clientes', (SELECT COUNT(*) FROM public.cliente),
  'empleados', (SELECT COUNT(*) FROM public.empleado),
  'sucursales', (SELECT COUNT(*) FROM public.sucursal),
  'productos', (SELECT COUNT(*) FROM public.producto),
  'variantes', (SELECT COUNT(*) FROM public.variante_producto),
  'inventarios', (SELECT COUNT(*) FROM public.inventario),
  'reservas', (SELECT COUNT(*) FROM public.reserva),
  'ventas', (SELECT COUNT(*) FROM public.venta),
  'pagos', (SELECT COUNT(*) FROM public.pago)
)::text;
'@

    $antes = ConsultarJson -Consulta $lecturaBase
    # host() elimina el sufijo CIDR /32 que inet::text puede incluir.
    # Mostrar la respuesta diagnostica si hay una discrepancia; continuar
    # solamente si tanto la base como la direccion coinciden.
    if ($antes.base -cne $base -or $antes.servidor -cne $servidor) {
        $detalle = ("Destino local inesperado. Base obtenida: '{0}'; host obtenido: '{1}'. " +
                    "Base esperada: '{2}'; host esperado: '{3}'. No se ejecuto SQL de migracion.") -f $antes.base, $antes.servidor, $base, $servidor
        throw $detalle
    }
    if ([int]$antes.tablas -ne 41 -or [int]$antes.saas -ne 0) {
        throw "Se esperaban 41 tablas y cero SaaS. Actual: $($antes.tablas) tablas / $($antes.saas) SaaS. Abortado."
    }

    Write-Host ''
    Write-Host 'PREVERIFICACION OK' -ForegroundColor Green
    Write-Host "Host: $servidor   Puerto: $puerto   Base: $base"
    Write-Host ('Conteos antes: ' + ($antes | ConvertTo-Json -Compress))
    Write-Host "Archivo: $migracion"
    Write-Host 'No se utilizara Supabase ni otras bases locales.'
    Write-Host ''
    $confirmacion = Read-Host 'Para aplicar SOLO a la base local escribe APLICAR'
    if ($confirmacion -cne 'APLICAR') {
        throw 'Cancelado sin ejecutar la migracion.'
    }

    Write-Host ''
    Write-Host 'Ejecutando transaccion SQL versionada en la base local...'
    & $psql --host=$servidor --port=$puerto --username=$usuario --dbname=$base --no-password --no-psqlrc --set=ON_ERROR_STOP=1 --file=$migracion
    if ($LASTEXITCODE -ne 0) {
        throw 'ERROR SQL. La migracion se interrumpio; revisar la salida y NO avanzar.'
    }

    $lecturaPost = @'
SELECT json_build_object(
  'base', current_database(),
  'servidor', host(inet_server_addr()),
  'tablas', (SELECT COUNT(*) FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public' AND c.relkind = 'r'),
  'saas', (SELECT COUNT(*) FROM information_schema.tables
    WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
      AND table_name IN ('empresa','plan_saas','suscripcion',
                         'usuario_empresa','empresa_configuracion')),
  'rls_nuevas', (SELECT COUNT(*) FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname='public' AND c.relname IN
      ('empresa','plan_saas','suscripcion','usuario_empresa','empresa_configuracion')
      AND c.relrowsecurity),
  'politicas_nuevas', (SELECT COUNT(*) FROM pg_policies
    WHERE schemaname = 'public' AND tablename IN
      ('empresa','plan_saas','suscripcion','usuario_empresa','empresa_configuracion')),
  'pk_nuevas', (SELECT COUNT(*) FROM pg_constraint k
    JOIN pg_class c ON c.oid=k.conrelid
    JOIN pg_namespace n ON n.oid=c.relnamespace
    WHERE n.nspname='public' AND k.contype='p'
      AND c.relname IN
        ('empresa','plan_saas','suscripcion','usuario_empresa','empresa_configuracion')),
  'fk_nuevas', (SELECT COUNT(*) FROM pg_constraint k
    JOIN pg_class c ON c.oid=k.conrelid
    JOIN pg_namespace n ON n.oid=c.relnamespace
    WHERE n.nspname='public' AND k.contype='f'
      AND c.relname IN
        ('empresa','plan_saas','suscripcion','usuario_empresa','empresa_configuracion')),
  'filas_saas', (
      (SELECT COUNT(*) FROM public.empresa)
    + (SELECT COUNT(*) FROM public.plan_saas)
    + (SELECT COUNT(*) FROM public.suscripcion)
    + (SELECT COUNT(*) FROM public.usuario_empresa)
    + (SELECT COUNT(*) FROM public.empresa_configuracion)
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
  'pagos', (SELECT COUNT(*) FROM public.pago)
)::text;
'@
    $despues = ConsultarJson -Consulta $lecturaPost
    if ($despues.base -cne $base -or $despues.servidor -cne $servidor) {
        $detalle = "Destino inesperado en comprobacion posterior. Base: '{0}'; host: '{1}'." -f $despues.base, $despues.servidor
        throw $detalle
    }

    $errores = @()
    if ([int]$despues.tablas -ne 46) { $errores += 'Se esperaban 46 tablas' }
    if ([int]$despues.saas -ne 5) { $errores += 'Se esperaban 5 tablas SaaS' }
    if ([int]$despues.rls_nuevas -ne 5) { $errores += 'RLS no esta activo en todas las tablas SaaS' }
    if ([int]$despues.politicas_nuevas -ne 0) { $errores += 'Hay politicas inesperadas en las tablas nuevas' }
    if ([int]$despues.pk_nuevas -ne 5) { $errores += 'Se esperaban 5 claves primarias SaaS' }
    if ([int]$despues.fk_nuevas -ne 6) { $errores += 'Se esperaban 6 claves foraneas SaaS' }
    if ([int]$despues.filas_saas -ne 0) { $errores += 'Las tablas SaaS deberian estar vacias' }
    foreach ($campo in @('usuarios','clientes','empleados','sucursales',
                        'productos','variantes','inventarios','reservas','ventas','pagos')) {
        if ($antes.$campo -ne $despues.$campo) {
            $errores += "Cambio inesperado de registros: $campo"
        }
    }
    Write-Host ''
    Write-Host ('Conteos despues: ' + ($despues | ConvertTo-Json -Compress))
    if ($errores.Count -gt 0) {
        Write-Host 'VALIDACION POSTERIOR FALLIDA:' -ForegroundColor Red
        foreach ($errorEncontrado in $errores) { Write-Host "- $errorEncontrado" }
        throw 'Detenerse: no avanzar a la asignacion de empresas.'
    }

    Write-Host ''
    Write-Host 'MIGRACION SAAS LOCAL COMPLETADA Y VERIFICADA' -ForegroundColor Green
    Write-Host "Base de prueba preservada: $base"
    Write-Host 'No se modifico Supabase. Mantener SAAS_AUTH_ENABLED=false.'
} finally {
    if ($null -eq $originalPassword) {
        Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue
    } else {
        $env:PGPASSWORD = $originalPassword
    }
    if ($null -eq $originalSsl) {
        Remove-Item Env:PGSSLMODE -ErrorAction SilentlyContinue
    } else {
        $env:PGSSLMODE = $originalSsl
    }
}
