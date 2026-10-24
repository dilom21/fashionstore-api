# FashionStore - BLOQUE 2 / PASO 7 (verificacion, sin cambios persistentes)
# Solo PostgreSQL LOCAL + tests_saas unitarios. NO usa Supabase.
$ErrorActionPreference = 'Stop'
$servidor = '127.0.0.1'
$puerto = 5432
$usuario = 'postgres'
$base = 'fashionstore_restore_20261023_193554_594'
$psql = 'C:\Program Files\PostgreSQL\18\bin\psql.exe'
$prueba = Join-Path $PSScriptRoot '06_pruebas_integridad_multitenant_local.sql'

if (-not (Test-Path -LiteralPath $psql -PathType Leaf)) {
  throw "Falta PostgreSQL local 18: $psql"
}
if (-not (Test-Path -LiteralPath $prueba -PathType Leaf)) {
  throw "Falta la prueba SQL del Paso 7: $prueba. Haz git pull."
}
$prueba = (Resolve-Path -LiteralPath $prueba).Path
if (-not (Test-Path 'tests_saas\test_auth_tenant_unit.py' -PathType Leaf)) {
  throw 'Ejecuta este script desde C:\SI2_Parcial1\fashionstore-api.'
}

$passOld = [Environment]::GetEnvironmentVariable('PGPASSWORD','Process')
$sslOld = [Environment]::GetEnvironmentVariable('PGSSLMODE','Process')
$secret = Read-Host 'Contrasena PostgreSQL LOCAL (NO Supabase)' -AsSecureString
$ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secret)
try { $env:PGPASSWORD = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr) }
finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr) }
$env:PGSSLMODE = 'disable'

try {
  function ResumenLocal {
    $sql = @'
SELECT json_build_object(
  'base',current_database(),
  'host',host(inet_server_addr()),
  'tablas',(SELECT count(*) FROM pg_class c JOIN pg_namespace n
    ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relkind='r'),
  'empresas',(SELECT count(*) FROM public.empresa),
  'suscripciones',(SELECT count(*) FROM public.suscripcion),
  'usuarios',(SELECT count(*) FROM public.usuario),
  'membresias',(SELECT count(*) FROM public.usuario_empresa),
  'sucursales',(SELECT count(*) FROM public.sucursal),
  'clientes',(SELECT count(*) FROM public.cliente),
  'empleados',(SELECT count(*) FROM public.empleado),
  'productos',(SELECT count(*) FROM public.producto),
  'variantes',(SELECT count(*) FROM public.variante_producto),
  'inventarios',(SELECT count(*) FROM public.inventario),
  'ventas',(SELECT count(*) FROM public.venta),
  'pagos',(SELECT count(*) FROM public.pago),
  'reservas',(SELECT count(*) FROM public.reserva),
  'bitacora',(SELECT count(*) FROM public.bitacora),
  'fk_tenant',(SELECT count(*) FROM pg_constraint c
     WHERE c.contype='f' AND c.conname LIKE 'fk_tenant_%' AND c.convalidated),
  'unique_tenant',(SELECT count(*) FROM pg_constraint c
     WHERE c.contype='u' AND c.conname LIKE 'uq_tenant_%' AND c.convalidated),
  'triggers',(SELECT count(*) FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid
     JOIN pg_namespace n ON n.oid=c.relnamespace
     WHERE n.nspname='public' AND NOT t.tgisinternal),
  'rol_actual_bypassrls',(SELECT rolbypassrls OR rolsuper FROM pg_roles WHERE rolname=current_user)
)::text;
'@
    $out = & $psql --host=$servidor --port=$puerto --username=$usuario --dbname=$base --no-password --no-psqlrc --no-align --tuples-only --set=ON_ERROR_STOP=1 --command=$sql
    if ($LASTEXITCODE -ne 0 -or -not $out) {
      throw 'Fallo una consulta local de verificacion.'
    }
    return (($out | Out-String).Trim() | ConvertFrom-Json)
  }

  $antes = ResumenLocal
  if ($antes.base -cne $base -or $antes.host -cne $servidor) {
    throw "Conexion inesperada: $($antes.base) / $($antes.host)"
  }
  $esperados = @{
    tablas=46; empresas=5; suscripciones=5; usuarios=13; membresias=13;
    sucursales=13; clientes=9; empleados=8; productos=18; variantes=124;
    inventarios=274; ventas=40; pagos=30; reservas=11; bitacora=844;
    fk_tenant=48; unique_tenant=28; triggers=27
  }
  foreach ($campo in $esperados.Keys) {
    if ([int]$antes.$campo -ne [int]$esperados[$campo]) {
      throw ("Datos inesperados antes de pruebas: {0} esperado={1}, real={2}" -f
        $campo,$esperados[$campo],$antes.$campo)
    }
  }
  Write-Host ''
  Write-Host 'PREVERIFICACION PASO 7 OK' -ForegroundColor Green
  Write-Host "Servidor: $servidor   Puerto: $puerto   Base: $base"
  Write-Host ('ANTES: '+($antes | ConvertTo-Json -Compress))
  Write-Host 'Tres INSERT cruzados deben ser rechazados por las FK tenant.'
  Write-Host 'Todas las pruebas SQL terminan expresamente en ROLLBACK.'
  Write-Host 'Despues se ejecutara pytest -q tests_saas contra SQLite aislado.'
  $confirmacion = Read-Host 'Escribe PROBAR para ejecutar SOLO pruebas locales reversibles'
  if ($confirmacion -cne 'PROBAR') {
    throw 'Cancelado. No se ejecutaron pruebas.'
  }

  Write-Host ''
  Write-Host '== Pruebas SQL de integridad con ROLLBACK =='
  & $psql --host=$servidor --port=$puerto --username=$usuario --dbname=$base --no-password --no-psqlrc --set=ON_ERROR_STOP=1 --file=$prueba
  if ($LASTEXITCODE -ne 0) {
    throw 'Fallo una prueba SQL. No avanzar; la transaccion incompleta sera descartada.'
  }

  $despues = ResumenLocal
  if ($despues.base -cne $base -or $despues.host -cne $servidor) {
    throw 'Destino inesperado tras la prueba.'
  }
  $fallos = @()
  foreach ($campo in $esperados.Keys) {
    if ([int]$despues.$campo -ne [int]$antes.$campo) {
      $fallos += "$campo antes=$($antes.$campo), despues=$($despues.$campo)"
    }
  }
  Write-Host ('DESPUES: '+($despues | ConvertTo-Json -Compress))
  if ($fallos.Count -gt 0) {
    throw ('Conteos alterados: '+($fallos -join '; '))
  }
  Write-Host 'SQL: 6 verificaciones y ROLLBACK correctos; conteos conservados.' -ForegroundColor Green

  Write-Host ''
  Write-Host '== Tests unitarios de autenticacion SaaS con SQLite =='
  & python -m pytest -q tests_saas
  if ($LASTEXITCODE -ne 0) {
    throw 'Fallo pytest tests_saas; revisar resultados antes de continuar.'
  }
  Write-Host ''
  Write-Host 'PASO 7: PRUEBAS ESTRUCTURALES Y UNITARIAS LOCALES SUPERADAS' -ForegroundColor Green
  if ($despues.rol_actual_bypassrls -eq $true) {
    Write-Host 'AVISO: conexion postgres tiene bypass RLS; NO prueba el aislamiento SELECT.'
  }
  Write-Host 'BLOQUEO: endpoints comerciales aun sin contexto de tenant obligatorio.'
  Write-Host 'BLOQUEO: validar reglas de planes, vigencia y autorizacion desde FastAPI.'
  Write-Host 'NO desplegar ni habilitar SAAS_AUTH_ENABLED. Supabase sin cambios.'
} finally {
  if ($null -eq $passOld) { Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue }
  else { $env:PGPASSWORD = $passOld }
  if ($null -eq $sslOld) { Remove-Item Env:PGSSLMODE -ErrorAction SilentlyContinue }
  else { $env:PGSSLMODE = $sslOld }
}
