# B3/P5I: RECUPERAR exclusivamente la base sandbox parcial de ensayo.
# NO BORRA BASES NI OBJETOS. Si public no esta totalmente vacio, ABORTAR.
# NO ejecuta sp_*_tenant ni toca BD de origen. Solicita doble opt-in.
param(
    [switch]$RecuperarSandboxParcial,
    [switch]$ConfirmoSandboxVacio
)
$ErrorActionPreference = 'Stop'
$servidor = '127.0.0.1'
$puerto = 5432
$usuario = 'postgres'
$origen = 'fashionstore_restore_20261023_193554_594'
$destino = 'fashionstore_saas_reservas_sandbox_5h'
$pgdir = 'C:\Program Files\PostgreSQL\18\bin'
$psql = Join-Path $pgdir 'psql.exe'
$pgdump = Join-Path $pgdir 'pg_dump.exe'
$pgrestore = Join-Path $pgdir 'pg_restore.exe'
$verificador = Join-Path $PSScriptRoot '14_verificar_sandbox_pg_reservas_local.sql'
$dump = Join-Path $env:TEMP 'fashionstore_5i_RESCATE_SCHEMA_ONLY.dump'
$toc = Join-Path $env:TEMP 'fashionstore_5i_RESCATE_PUBLIC_FILTERED.list'

Write-Host 'RECUPERACION CONTROLADA de sandbox parcial SOLO LOCAL.'
Write-Host "ORIGEN READ ONLY: $servidor/$origen"
Write-Host "DESTINO EXISTENTE: $servidor/$destino"
Write-Host 'No se borraran bases, esquemas ni tablas.'
if (-not $RecuperarSandboxParcial) {
    Write-Host 'MODO PLAN SIN CAMBIOS: se requiere -RecuperarSandboxParcial -ConfirmoSandboxVacio.'
    return
}
if (-not $ConfirmoSandboxVacio) {
    throw 'Falta el segundo consentimiento -ConfirmoSandboxVacio. No hubo cambios.'
}
if ($servidor -ne '127.0.0.1' -or
    $origen -ne 'fashionstore_restore_20261023_193554_594' -or
    $destino -ne 'fashionstore_saas_reservas_sandbox_5h' -or
    $destino -eq $origen) {
    throw 'Origen/destino no autorizados. Abortado.'
}
foreach ($exe in @($psql,$pgdump,$pgrestore,$verificador)) {
    if (-not (Test-Path -LiteralPath $exe -PathType Leaf)) {
        throw "Falta utilidad/SQL: $exe"
    }
}
if ((Test-Path -LiteralPath $dump) -or
    (Test-Path -LiteralPath $toc)) {
    throw 'Archivo temporal previo existente: no sobrescribir.'
}
$passOld = [Environment]::GetEnvironmentVariable('PGPASSWORD','Process')
$sslOld = [Environment]::GetEnvironmentVariable('PGSSLMODE','Process')
$optsOld = [Environment]::GetEnvironmentVariable('PGOPTIONS','Process')
$clave = Read-Host 'Contrasena PostgreSQL LOCAL (NO Supabase)' -AsSecureString
$ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($clave)
try {
    $env:PGPASSWORD = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)
} finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr)
}
$env:PGSSLMODE = 'disable'
try {
    $env:PGOPTIONS = '-c default_transaction_read_only=on -c statement_timeout=60000'

    # Revalidar COPIA ORIGINAL sin leer PII: el origen nunca recibe DML.
    $sqlOrigen = @'
SELECT CASE WHEN current_database()='fashionstore_restore_20261023_193554_594'
 AND host(inet_server_addr())='127.0.0.1'
 AND (SELECT count(*) FROM public.empresa)=5
 AND (SELECT count(*) FROM public.reserva)=11
 AND (SELECT count(*) FROM public.inventario)=274
 AND (SELECT count(*) FROM pg_constraint co
      JOIN pg_class c ON c.oid=co.conrelid
      JOIN pg_namespace n ON n.oid=c.relnamespace
      WHERE n.nspname='public' AND co.contype='f'
        AND co.convalidated AND co.conname LIKE 'fk_tenant_%')=48
 THEN 'ORIGEN_LOCAL_VALIDO' ELSE 'ORIGEN_NO_VALIDO' END;
'@
    $estadoOrigen = & $psql --host=$servidor --port=$puerto --username=$usuario --dbname=$origen --no-password --no-psqlrc --set=ON_ERROR_STOP=1 --tuples-only --no-align --command=$sqlOrigen
    if ($LASTEXITCODE -ne 0 -or (($estadoOrigen -join '')).Trim() -ne 'ORIGEN_LOCAL_VALIDO') {
        throw 'ORIGEN NO VALIDO: no tocar sandbox.'
    }

    # RECHAZAR cualquier objeto public previo. No limpiar ni sobrescribir.
    # Antes de pg_restore fallido, CREATE SCHEMA public fue la primera orden.
    # Comprobarlo, NO suponerlo: tablas, vistas, secuencias, funciones,
    # tipos e incluso extensiones deben estar ausentes.
    $sqlDestino = @'
WITH x AS (
 SELECT
  (SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
   WHERE n.nspname='public') AS relaciones,
  (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
   WHERE n.nspname='public') AS rutinas,
  (SELECT count(*) FROM pg_type t JOIN pg_namespace n ON n.oid=t.typnamespace
   WHERE n.nspname='public') AS tipos,
  (SELECT count(*) FROM pg_extension e JOIN pg_namespace n ON n.oid=e.extnamespace
   WHERE n.nspname='public') AS extensiones,
  (SELECT count(*) FROM pg_namespace WHERE nspname='public') AS public_existe
)
SELECT CASE WHEN current_database()='fashionstore_saas_reservas_sandbox_5h'
 AND host(inet_server_addr())='127.0.0.1'
 AND relaciones=0 AND rutinas=0 AND tipos=0
 AND extensiones=0 AND public_existe=1
 THEN 'PARCIAL_PUBLIC_VACIO_AUTORIZADO' ELSE 'SANDBOX_NO_VACIO' END
FROM x;
'@
    $estadoDestino = & $psql --host=$servidor --port=$puerto --username=$usuario --dbname=$destino --no-password --no-psqlrc --set=ON_ERROR_STOP=1 --tuples-only --no-align --command=$sqlDestino
    if ($LASTEXITCODE -ne 0 -or (($estadoDestino -join '')).Trim() -ne 'PARCIAL_PUBLIC_VACIO_AUTORIZADO') {
        throw 'SANDBOX NO VACIO o no coincide nombre/local: NO RECUPERAR. Sin cambios.'
    }

    Write-Host 'Controles APROBADOS: origen intacto, sandbox public sin objetos.'
    Write-Host 'Exportando SOLO esquema public; NO se copian filas...'
    & $pgdump --host=$servidor --port=$puerto --username=$usuario --dbname=$origen --no-password --schema-only --schema=public --format=custom --file=$dump
    if ($LASTEXITCODE -ne 0) { throw 'pg_dump fallo. Sandbox sin cambios.' }
    $indice = @(& $pgrestore --list $dump)
    if ($LASTEXITCODE -ne 0) { throw 'No se puede inspeccionar el dump. Sin restauracion.' }
    $texto = $indice -join "`n"
    if ($texto -match '(?im)^\d+;.*\b(?:TABLE DATA|MATERIALIZED VIEW DATA|BLOBS?)\b') {
        throw 'El dump tiene datos: abortar.'
    }
    # El esquema public ya existe por defecto en template0 de PG18.
    # Quitar SOLO la entrada que re-ejecutaria CREATE SCHEMA public.
    $patron = '^\d+;\s+\d+\s+\d+\s+SCHEMA\s+-\s+public(?:\s|$)'
    $omitidas = @($indice | Where-Object { $_ -match $patron })
    if ($omitidas.Count -ne 1) {
        throw 'El dump no contiene exactamente 1 entrada SCHEMA - public: abortar.'
    }
    $seleccion = [string[]]@($indice | Where-Object { $_ -notmatch $patron })
    [System.IO.File]::WriteAllLines(
        $toc, $seleccion, (New-Object System.Text.UTF8Encoding($false))
    )

    # NUNCA --clean, --create, DROP, ni reparaciones de esquema existentes.
    # --single-transaction revierte todos los DDL ante errores posteriores.
    $env:PGOPTIONS = '-c statement_timeout=60000'
    & $pgrestore --host=$servidor --port=$puerto --username=$usuario --dbname=$destino --no-password --no-owner --no-acl --exit-on-error --single-transaction --use-list=$toc $dump
    if ($LASTEXITCODE -ne 0) {
        throw 'pg_restore fallo y revirtio la transaccion: no reintentar ni modificar DB sin revisar error.'
    }
    # Validacion INDEPENDIENTE read-only antes de anunciar exito.
    $env:PGOPTIONS = '-c default_transaction_read_only=on -c statement_timeout=60000'
    & $psql --host=$servidor --port=$puerto --username=$usuario --dbname=$destino --no-password --no-psqlrc --set=ON_ERROR_STOP=1 --file=$verificador
    if ($LASTEXITCODE -ne 0) {
        throw 'Restauracion ejecutada, pero verificador fallo: SANDBOX NO APTO. No usarlo.'
    }
    Write-Host ''
    Write-Host 'RECUPERACION SANDBOX LOCAL SUPERADA (SIN DATOS HISTORICOS)' -ForegroundColor Green
    Write-Host 'Origen intacto; NO se instalaron ni ejecutaron sp_*_tenant.'
    Write-Host 'AUN NO habilitar SaaS ni usar el rol postgres para probar RLS.'
} finally {
    foreach ($archivo in @($toc,$dump)) {
        if (Test-Path -LiteralPath $archivo) {
            Remove-Item -LiteralPath $archivo -Force -ErrorAction SilentlyContinue
        }
    }
    if ($null -eq $passOld) { Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue }
    else { $env:PGPASSWORD = $passOld }
    if ($null -eq $sslOld) { Remove-Item Env:PGSSLMODE -ErrorAction SilentlyContinue }
    else { $env:PGSSLMODE = $sslOld }
    if ($null -eq $optsOld) { Remove-Item Env:PGOPTIONS -ErrorAction SilentlyContinue }
    else { $env:PGOPTIONS = $optsOld }
}
