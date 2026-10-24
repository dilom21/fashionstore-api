# FashionStore / Bloque 2 / Paso 6
# Datos FICTICIOS en BASE LOCAL aislada. No usa Supabase ni URLs remotas.
$ErrorActionPreference = 'Stop'
$hostLocal = '127.0.0.1'
$puerto = 5432
$usuario = 'postgres'
$base = 'fashionstore_restore_20261023_193554_594'
$psql = 'C:\Program Files\PostgreSQL\18\bin\psql.exe'
$archivo = Join-Path $PSScriptRoot '..\..\supabase\migrations\20261023000400_cuatro_tenants_demo_local.sql'
if (-not (Test-Path -LiteralPath $psql -PathType Leaf)) {
  throw "No se encontro el ejecutable PostgreSQL 18: $psql"
}
if (-not (Test-Path -LiteralPath $archivo -PathType Leaf)) {
  throw 'No se encontro la migracion del Paso 6. Haz git pull.'
}
$archivo = (Resolve-Path -LiteralPath $archivo).Path
$oldPass = [Environment]::GetEnvironmentVariable('PGPASSWORD','Process')
$oldSSL = [Environment]::GetEnvironmentVariable('PGSSLMODE','Process')
$pass = Read-Host 'Contrasena del postgres LOCAL (NO Supabase)' -AsSecureString
$handle = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($pass)
try { $env:PGPASSWORD = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($handle) }
finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($handle) }
$env:PGSSLMODE = 'disable'
try {
  function Consultar {
    param([Parameter(Mandatory=$true)][string]$SQL)
    $out = & $psql --host=$hostLocal --port=$puerto --username=$usuario --dbname=$base --no-password --no-psqlrc --tuples-only --no-align --set=ON_ERROR_STOP=1 --command=$SQL
    if ($LASTEXITCODE -ne 0 -or -not $out) {
      throw 'Error al consultar la base PostgreSQL LOCAL.'
    }
    return (($out | Out-String).Trim() | ConvertFrom-Json)
  }

  $sqlAntes = @'
SELECT json_build_object(
 'base',current_database(),
 'host',host(inet_server_addr()),
 'tablas',(SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relkind='r'),
 'empresas',(SELECT count(*) FROM public.empresa),
 'planes',(SELECT count(*) FROM public.plan_saas),
 'suscripciones',(SELECT count(*) FROM public.suscripcion),
 'membresias',(SELECT count(*) FROM public.usuario_empresa),
 'usuarios',(SELECT count(*) FROM public.usuario),
 'empleados',(SELECT count(*) FROM public.empleado),
 'clientes',(SELECT count(*) FROM public.cliente),
 'sucursales',(SELECT count(*) FROM public.sucursal),
 'categorias',(SELECT count(*) FROM public.categoria),
 'temporadas',(SELECT count(*) FROM public.temporada),
 'productos',(SELECT count(*) FROM public.producto),
 'variantes',(SELECT count(*) FROM public.variante_producto),
 'inventarios',(SELECT count(*) FROM public.inventario),
 'ventas',(SELECT count(*) FROM public.venta),
 'pagos',(SELECT count(*) FROM public.pago),
 'reservas',(SELECT count(*) FROM public.reserva),
 'bitacora',(SELECT count(*) FROM public.bitacora),
 'auditoria_global',(SELECT count(*) FROM public.bitacora WHERE empresa_id IS NULL),
 'fk_tenant',(SELECT count(*) FROM pg_constraint WHERE conname LIKE 'fk_tenant_%'),
 'unique_tenant',(SELECT count(*) FROM pg_constraint WHERE conname LIKE 'uq_tenant_%'),
 'triggers',(SELECT count(*) FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid
             JOIN pg_namespace n ON n.oid=c.relnamespace
             WHERE n.nspname='public' AND NOT t.tgisinternal),
 'admins_demo',(SELECT count(*) FROM public.usuario WHERE correo LIKE 'admin.%@demo.invalid')
)::text;
'@
  $antes=Consultar -SQL $sqlAntes
  if ($antes.base -cne $base -or $antes.host -cne $hostLocal) {
    throw ("Conexion no autorizada: {0} en {1}" -f $antes.base,$antes.host)
  }
  $esperadosAntes=@{
    tablas=46; empresas=1; planes=2; suscripciones=1; membresias=9;
    usuarios=9; empleados=4; clientes=5; sucursales=4; productos=14;
    variantes=120; inventarios=265; ventas=40; pagos=30; reservas=11;
    bitacora=806; auditoria_global=0; fk_tenant=48; unique_tenant=28;
    triggers=27; admins_demo=0
  }
  foreach ($campo in $esperadosAntes.Keys) {
    if ([int]$antes.$campo -ne [int]$esperadosAntes[$campo]) {
      throw ("Preverificacion fallida en {0}: esperado {1}, actual {2}." -f
        $campo,$esperadosAntes[$campo],$antes.$campo)
    }
  }
  Write-Host ''
  Write-Host 'PREVERIFICACION OK' -ForegroundColor Green
  Write-Host "Servidor: $hostLocal   Puerto: $puerto   Base: $base"
  Write-Host ('ANTES: '+($antes | ConvertTo-Json -Compress))
  Write-Host "Migracion: $archivo"
  Write-Host 'Solo datos ficticios. Cuatro cuentas administrativas INACTIVAS.'
  Write-Host 'Se preservan Vanter Men y las transacciones del primer parcial.'
  Write-Host ''
  $confirmar=Read-Host 'Escribe DEMO para sembrar las 4 empresas SOLO en PostgreSQL LOCAL'
  if ($confirmar -cne 'DEMO') { throw 'Operacion cancelada sin ejecutar la migracion.' }

  & $psql --host=$hostLocal --port=$puerto --username=$usuario --dbname=$base --no-password --no-psqlrc --set=ON_ERROR_STOP=1 --file=$archivo
  if ($LASTEXITCODE -ne 0) {
    throw 'ERROR SQL: se debe haber revertido la transaccion; no avanzar.'
  }
  $despues=Consultar -SQL $sqlAntes
  if ($despues.base -cne $base -or $despues.host -cne $hostLocal) {
    throw 'La postverificacion no corresponde a la base local prevista.'
  }
  $esperadosDespues=@{
    tablas=46; empresas=5; planes=2; suscripciones=5; membresias=13;
    usuarios=13; empleados=8; clientes=9; sucursales=13; productos=18;
    variantes=124; inventarios=274; ventas=40; pagos=30; reservas=11;
    bitacora=840; auditoria_global=4; fk_tenant=48; unique_tenant=28;
    triggers=27; admins_demo=4
  }
  $fallos=@()
  foreach ($campo in $esperadosDespues.Keys) {
    if ([int]$despues.$campo -ne [int]$esperadosDespues[$campo]) {
      $fallos += "$campo esperado=$($esperadosDespues[$campo]) actual=$($despues.$campo)"
    }
  }
  foreach ($campo in @('categorias','temporadas')) {
    if ([int]$despues.$campo -ne ([int]$antes.$campo+4)) {
      $fallos += "El conteo de $campo no aumento exactamente en 4"
    }
  }
  Write-Host ''
  Write-Host ('DESPUES: '+($despues | ConvertTo-Json -Compress))
  if ($fallos.Count -gt 0) {
    foreach ($error in $fallos) { Write-Host ("ERROR: "+$error) -ForegroundColor Red }
    throw 'Postverificacion fallida, no avanzar.'
  }
  $resumenSQL = @'
SELECT json_agg(x ORDER BY x.slug)::text
FROM (
 SELECT e.slug,p.codigo AS plan,
   (SELECT count(*) FROM public.sucursal s WHERE s.empresa_id=e.id AND s.estado) AS sucursales_activas,
   p.max_sucursales_activas AS max_sucursales,
   (SELECT count(*) FROM public.producto pr WHERE pr.empresa_id=e.id) AS productos,
   (SELECT count(*) FROM public.inventario inv WHERE inv.empresa_id=e.id) AS inventarios,
   (SELECT count(*) FROM public.usuario_empresa u WHERE u.empresa_id=e.id) AS membresias,
   (SELECT count(*) FROM public.venta v WHERE v.empresa_id=e.id) AS ventas
 FROM public.empresa e
 JOIN public.suscripcion ss ON ss.empresa_id=e.id AND ss.estado='ACTIVA'
 JOIN public.plan_saas p ON p.id=ss.plan_saas_id
) x;
'@
  $detalles=Consultar -SQL $resumenSQL
  if ($detalles.Count -ne 5) { throw 'No se pudieron verificar los cinco tenants.' }
  Write-Host ''
  Write-Host 'CINCO EMPRESAS PREPARADAS EN POSTGRESQL LOCAL' -ForegroundColor Green
  $detalles | Format-Table slug,plan,sucursales_activas,max_sucursales,productos,inventarios,membresias,ventas -AutoSize | Out-Host
  Write-Host 'No se copiaron ventas ni usuarios reales. Admin demo inactivos.'
  Write-Host 'Mantener SAAS_AUTH_ENABLED=false. Supabase sin cambios.'
} finally {
  if ($null -eq $oldPass) { Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue }
  else { $env:PGPASSWORD=$oldPass }
  if ($null -eq $oldSSL) { Remove-Item Env:PGSSLMODE -ErrorAction SilentlyContinue }
  else { $env:PGSSLMODE=$oldSSL }
}
