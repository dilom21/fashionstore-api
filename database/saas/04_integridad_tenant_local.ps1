# FashionStore — B2 Paso 5. SOLO base local restaurada de PostgreSQL 18.
$ErrorActionPreference = 'Stop'
$servidor = '127.0.0.1'
$puerto = 5432
$usuario = 'postgres'
$base = 'fashionstore_restore_20261023_193554_594'
$psql = 'C:\Program Files\PostgreSQL\18\bin\psql.exe'
$archivo = Join-Path $PSScriptRoot '..\..\supabase\migrations\20261023000300_integridad_tenant_local.sql'
if (-not (Test-Path -LiteralPath $psql -PathType Leaf)) { throw 'No esta instalado PostgreSQL local 18 en la ruta esperada.' }
if (-not (Test-Path -LiteralPath $archivo -PathType Leaf)) { throw 'No existe el SQL del Paso 5. Ejecuta git pull.' }
$archivo = (Resolve-Path -LiteralPath $archivo).Path
$antPass = [Environment]::GetEnvironmentVariable('PGPASSWORD','Process')
$antSSL = [Environment]::GetEnvironmentVariable('PGSSLMODE','Process')
$clave = Read-Host 'Contrasena LOCAL postgres (NO Supabase)' -AsSecureString
$ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($clave)
try { $env:PGPASSWORD = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr) }
finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr) }
$env:PGSSLMODE = 'disable'
try {
 function Comprobar {
  param([Parameter(Mandatory=$true)][string]$SQL)
  $salida = & $psql --host=$servidor --port=$puerto --username=$usuario --dbname=$base --no-password --no-psqlrc --no-align --tuples-only --set=ON_ERROR_STOP=1 --command=$SQL
  if ($LASTEXITCODE -ne 0 -or -not $salida) { throw 'No se pudo consultar PostgreSQL LOCAL.' }
  return (($salida | Out-String).Trim() | ConvertFrom-Json)
 }
 $antesSQL = @'
SELECT json_build_object(
 'base',current_database(),'host',host(inet_server_addr()),
 'tablas',(SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relkind='r'),
 'empresas',(SELECT count(*) FROM public.empresa),
 'plan_profesional',(SELECT count(*) FROM public.suscripcion s JOIN public.empresa e ON e.id=s.empresa_id JOIN public.plan_saas p ON p.id=s.plan_saas_id WHERE e.slug='vanter-men' AND p.codigo='PROFESIONAL'),
 'membresias',(SELECT count(*) FROM public.usuario_empresa),
 'triggers',(SELECT count(*) FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND NOT t.tgisinternal),
 'usuarios',(SELECT count(*) FROM public.usuario),
 'clientes',(SELECT count(*) FROM public.cliente),
 'sucursales',(SELECT count(*) FROM public.sucursal),
 'productos',(SELECT count(*) FROM public.producto),
 'inventarios',(SELECT count(*) FROM public.inventario),
 'ventas',(SELECT count(*) FROM public.venta),
 'pagos',(SELECT count(*) FROM public.pago),
 'bitacora',(SELECT count(*) FROM public.bitacora),
 'fk_compuestas',(SELECT count(*) FROM pg_constraint c JOIN pg_class t ON t.oid=c.conrelid JOIN pg_namespace n ON n.oid=t.relnamespace WHERE n.nspname='public' AND c.contype='f' AND c.conname LIKE 'fk_tenant_%')
)::text;
'@
 $antes=Comprobar -SQL $antesSQL
 if ($antes.base -cne $base -or $antes.host -cne $servidor) {
  throw ("Destino incorrecto: {0} en {1}" -f $antes.base,$antes.host)
 }
 if ([int]$antes.tablas -ne 46 -or [int]$antes.empresas -ne 1 -or
     [int]$antes.plan_profesional -ne 1 -or [int]$antes.membresias -ne 9 -or
     [int]$antes.triggers -ne 27 -or [int]$antes.fk_compuestas -ne 0 -or
     [int]$antes.ventas -ne 40 -or [int]$antes.pagos -ne 30) {
  throw 'Preverificacion fallida. No se aplico la migracion.'
 }
 Write-Host ''
 Write-Host 'PREVERIFICACION OK' -ForegroundColor Green
 Write-Host "Host: $servidor   Base: $base"
 Write-Host ('ANTES: '+($antes|ConvertTo-Json -Compress))
 Write-Host "Archivo SQL: $archivo"
 Write-Host 'Se anadiran FK compuestas, UNIQUE empresariales y restricciones de membresia.'
 Write-Host 'No modifica la base real ni afecta tablas de Supabase.'
 $respuesta=Read-Host 'Escribe AISLAR para aplicar SOLO en esta base LOCAL'
 if ($respuesta -cne 'AISLAR') { throw 'Cancelado. No se ejecuto la migracion.' }
 & $psql --host=$servidor --port=$puerto --username=$usuario --dbname=$base --no-password --no-psqlrc --set=ON_ERROR_STOP=1 --file=$archivo
 if ($LASTEXITCODE -ne 0) { throw 'Error SQL. No continuar: la transaccion debe haberse revertido.' }
 $despues=Comprobar -SQL $antesSQL
 if ($despues.base -cne $base -or $despues.host -cne $servidor) {
  throw 'Host/base inesperados en verificacion posterior.'
 }
 $fallos=@()
 foreach ($campo in @('tablas','empresas','plan_profesional','membresias','triggers',
                     'usuarios','clientes','sucursales','productos','inventarios',
                     'ventas','pagos','bitacora')) {
  if ($antes.$campo -ne $despues.$campo) { $fallos+="Cambio inesperado en $campo" }
 }
 if ([int]$despues.fk_compuestas -le 30) { $fallos+='Faltan FK tenant compuestas' }
 Write-Host ('DESPUES: '+($despues|ConvertTo-Json -Compress))
 if ($fallos.Count -gt 0) { throw ('Verificacion incompleta: '+($fallos -join '; ')) }
 Write-Host ''
 Write-Host 'INTEGRIDAD TENANT LOCAL APLICADA Y VERIFICADA' -ForegroundColor Green
 Write-Host 'Mantener SAAS_AUTH_ENABLED=false. No se modifico Supabase.'
} finally {
 if ($null -eq $antPass) { Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue }
 else { $env:PGPASSWORD=$antPass }
 if ($null -eq $antSSL) { Remove-Item Env:PGSSLMODE -ErrorAction SilentlyContinue }
 else { $env:PGSSLMODE=$antSSL }
}
