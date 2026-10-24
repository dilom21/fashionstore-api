# B3 / P5I: sandbox LOCAL de esquema; requiere doble consentimiento explicito.
# SIN DATOS personales. POR DEFECTO no realiza ninguna operacion.
# SOLO con -CrearSandbox -ConfirmoCopiaSinDatos crea NUEVA DB de ensayo.
# Jamás edita origen, no instala sp_*_tenant, no modifica Supabase.
param(
    [switch]$CrearSandbox,
    [switch]$ConfirmoCopiaSinDatos
)
$ErrorActionPreference = 'Stop'
$pgdir = 'C:\Program Files\PostgreSQL\18\bin'
$psql = Join-Path $pgdir 'psql.exe'
$pgdump = Join-Path $pgdir 'pg_dump.exe'
$createdb = Join-Path $pgdir 'createdb.exe'
$pgrestore = Join-Path $pgdir 'pg_restore.exe'
$verificador = Join-Path $PSScriptRoot '14_verificar_sandbox_pg_reservas_local.sql'
$servidor = '127.0.0.1'
$puerto = 5432
$usuario = 'postgres'
$origen = 'fashionstore_restore_20261023_193554_594'
$destino = 'fashionstore_saas_reservas_sandbox_5h'

Write-Host "Origen solo lectura: $servidor/$origen"
Write-Host "Destino NUEVO de ensayo: $servidor/$destino"
Write-Host 'La copia sera SOLO DE ESQUEMA, sin filas historicas de las tablas.'
Write-Host 'Atencion: las definiciones de funciones pueden contener configuraciones.'
Write-Host 'NO ejecuta los archivos .sql.txt de los procedimientos nuevos.'
if (-not $CrearSandbox) {
    Write-Host ''
    Write-Host 'MODO PLAN SIN CAMBIOS: aun NO se creo ninguna base.' -ForegroundColor Yellow
    Write-Host 'Para crear una DB NUEVA en esta subfase se requieren ambos switches y autorizacion del usuario.'
    Write-Host 'Mantener SAAS_AUTH_ENABLED=false y no instalar procedimientos.'
    return
}
if (-not $ConfirmoCopiaSinDatos) {
    throw 'Falta -ConfirmoCopiaSinDatos. Se requiere doble confirmacion explicita.'
}
if ($servidor -ne '127.0.0.1' -or
    $origen -ne 'fashionstore_restore_20261023_193554_594' -or
    $destino -ne 'fashionstore_saas_reservas_sandbox_5h' -or
    $destino -eq $origen) {
    throw 'Guardas de base de datos invalidadas. Operacion cancelada.'
}
if (-not (Test-Path -LiteralPath $verificador -PathType Leaf)) {
    throw 'Falta verificador READ ONLY 5I; git pull antes de crear la base.'
}
foreach ($ejecutable in @($psql,$pgdump,$createdb,$pgrestore)) {
    if (-not (Test-Path -LiteralPath $ejecutable -PathType Leaf)) {
        throw "Falta herramienta PostgreSQL 18 local: $ejecutable"
    }
}
if ([string]::IsNullOrWhiteSpace($env:TEMP)) {
    throw 'Directorio TEMP inexistente'
}
$anterior = [Environment]::GetEnvironmentVariable('PGPASSWORD','Process')
$sslAnterior = [Environment]::GetEnvironmentVariable('PGSSLMODE','Process')
$optionsAnterior = [Environment]::GetEnvironmentVariable('PGOPTIONS','Process')
$dump = Join-Path $env:TEMP 'fashionstore_saas_reservas_5h_SCHEMA_ONLY.dump'
$clave = Read-Host 'Contrasena de PostgreSQL LOCAL (no Supabase)' -AsSecureString
$ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($clave)
try {
    $env:PGPASSWORD = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)
} finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr)
}
$env:PGSSLMODE = 'disable'
try {
    $env:PGOPTIONS = '-c default_transaction_read_only=on -c statement_timeout=60000'
    $origenValido = @'
SELECT CASE WHEN current_database()='fashionstore_restore_20261023_193554_594'
 AND host(inet_server_addr())='127.0.0.1'
 AND (SELECT count(*) FROM public.empresa)=5
 AND (SELECT count(*) FROM public.reserva)=11
 AND (SELECT count(*) FROM public.inventario)=274
 AND (SELECT count(*) FROM pg_constraint WHERE conname LIKE 'fk_tenant_%'
      AND contype='f' AND convalidated)=48
 THEN 'ORIGEN_LOCAL_VALIDADO' ELSE 'ORIGEN_INVALIDO' END;
'@
    $respuesta = & $psql --host=$servidor --port=$puerto --username=$usuario --dbname=$origen --no-password --no-psqlrc --set=ON_ERROR_STOP=1 --tuples-only --no-align --command=$origenValido
    if ($LASTEXITCODE -ne 0 -or (($respuesta -join '')).Trim() -ne 'ORIGEN_LOCAL_VALIDADO') {
        throw 'El origen local no supero las guardas. No se crea sandbox.'
    }

    $existente = & $psql --host=$servidor --port=$puerto --username=$usuario --dbname=postgres --no-password --no-psqlrc --set=ON_ERROR_STOP=1 --tuples-only --no-align --command="SELECT count(*) FROM pg_database WHERE datname='fashionstore_saas_reservas_sandbox_5h';"
    if ($LASTEXITCODE -ne 0 -or (($existente -join '')).Trim() -ne '0') {
        throw 'El sandbox ya existe o no se pudo consultar. No sobrescribir.'
    }
    if (Test-Path -LiteralPath $dump) {
        throw 'Ya existe un dump temporal con ese nombre. Revisar manualmente.'
    }
    Write-Host 'Exportando SOLO ESQUEMA desde el origen, sin filas...'
    & $pgdump --host=$servidor --port=$puerto --username=$usuario --dbname=$origen --no-password --schema-only --schema=public --format=custom --file=$dump
    if ($LASTEXITCODE -ne 0) { throw 'pg_dump schema-only fallo; no se creo base de destino.' }
    # Revisar indice del archivo antes de crear cualquier base: solo objetos
    # del esquema, sin entradas de datos. pg_restore --list no restaura nada.
    $indice = & $pgrestore --list $dump
    if ($LASTEXITCODE -ne 0) { throw 'No se pudo inspeccionar el dump temporal.' }
    $indiceTexto = $indice -join "`n"
    if ($indiceTexto -match '(?im)^\d+;.*\b(?:TABLE DATA|MATERIALIZED VIEW DATA|BLOBS?)\b') {
        throw 'El dump contiene datos: abortar antes de crear el sandbox.'
    }

    # No se usa el origen como TEMPLATE: se crea DB nueva vacia y se importa
    # exclusivamente el esquema. pg_restore omitira datos historicos.
    $env:PGOPTIONS = '-c statement_timeout=60000'
    & $createdb --host=$servidor --port=$puerto --username=$usuario --no-password --template=template0 $destino
    if ($LASTEXITCODE -ne 0) { throw 'createdb fallo. El origen sigue intacto.' }
    & $pgrestore --host=$servidor --port=$puerto --username=$usuario --dbname=$destino --no-password --no-owner --no-acl --exit-on-error $dump
    if ($LASTEXITCODE -ne 0) {
        throw 'pg_restore fallo: sandbox parcial, no usar; revisar sin eliminar automaticamente.'
    }
    $filas = & $psql --host=$servidor --port=$puerto --username=$usuario --dbname=$destino --no-password --no-psqlrc --set=ON_ERROR_STOP=1 --tuples-only --no-align --command="SELECT (SELECT count(*) FROM public.empresa) + (SELECT count(*) FROM public.cliente) + (SELECT count(*) FROM public.reserva) + (SELECT count(*) FROM public.inventario);"
    if ($LASTEXITCODE -ne 0 -or (($filas -join '')).Trim() -ne '0') {
        throw 'Sandbox no quedo vacio: NO USAR. Investigar antes de cualquier prueba.'
    }
    # Verificar TODAS las tablas, constraints, RLS y ausencia de SP tenant.
    # Nunca declarar apto un sandbox parcial o con datos.
    $env:PGOPTIONS = '-c default_transaction_read_only=on -c statement_timeout=60000'
    & $psql --host=$servidor --port=$puerto --username=$usuario --dbname=$destino --no-password --no-psqlrc --set=ON_ERROR_STOP=1 --file=$verificador
    if ($LASTEXITCODE -ne 0) {
        throw 'Sandbox NO APTO: verificacion integral READ ONLY no superada; no usar para pruebas.'
    }
    Write-Host ''
    Write-Host 'SANDBOX LOCAL DE ESQUEMA PREPARADO (SIN DATOS HISTORICOS)' -ForegroundColor Green
    Write-Host "Destino: $servidor/$destino"
    Write-Host 'AUN NO AUTORIZA ejecucion de los SP, roles ni prueba de reservas.'
} finally {
    if (Test-Path -LiteralPath $dump) {
        Remove-Item -LiteralPath $dump -Force -ErrorAction SilentlyContinue
    }
    if ($null -eq $anterior) { Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue }
    else { $env:PGPASSWORD = $anterior }
    if ($null -eq $sslAnterior) { Remove-Item Env:PGSSLMODE -ErrorAction SilentlyContinue }
    else { $env:PGSSLMODE = $sslAnterior }
    if ($null -eq $optionsAnterior) { Remove-Item Env:PGOPTIONS -ErrorAction SilentlyContinue }
    else { $env:PGOPTIONS = $optionsAnterior }
}
