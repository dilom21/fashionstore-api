<#
5BQ / Etapa H - Extraccion AUTORIZADA del esquema publico real (corrige 80/81)

QUE CORRIGE RESPECTO DE 80_extraccion_esquema_autorizada_5bo.ps1
  1. 80 no filtra por esquema: un pg_dump sin --schema arrastra auth, storage,
     extensions y demas schemas administrados por Supabase. Aqui se exige
     -Esquemas y por defecto es solo 'public'.
  2. 80 usa por defecto los binarios de PostgreSQL 18. El origen verificado es
     PostgreSQL 17.6; aqui se exige pg_dump 17.x y falla si no lo es.
  3. 80 produce catalogo de texto (psql), que 87 NO puede consumir. Aqui ademas
     se ejecuta 92_catalogo_json_solo_lectura_5bq.sql para emitir el JSON con el
     contrato exacto de 87, y 94 valida ambos artefactos antes de dar por bueno
     el insumo.
  4. 80 no comprobaba que el dump fuera realmente schema-only ni que la carpeta
     de salida estuviera fuera del repositorio. Aqui se comprueban ambas cosas.

REGLAS INVIOLABLES
  * Requiere -Autorizado. Sin el switch no hace nada.
  * Rechaza loopback: los laboratorios tienen su propio runner.
  * La contrasena se pide de forma interactiva, vive solo en PGPASSWORD del
    proceso hijo y se borra en 'finally'. Nunca se imprime ni se escribe.
  * La salida debe quedar FUERA del repositorio. No se versiona ni se comparte.
  * Solo lectura: pg_dump --schema-only y SELECT de catalogo. Cero DDL/DML.

USO
  # 1) Ver el plan sin conectarse a nada
  powershell -ExecutionPolicy Bypass -File database/saas/93_extraer_esquema_publico_5bq.ps1 `
      -Servidor "db.<proyecto>.supabase.co" -Puerto 5432 -BaseDatos "postgres" `
      -Usuario "postgres" `
      -CarpetaSalida "$env:USERPROFILE\Documents\fashionstore-esquema-5bq" -SoloPlan

  # 2) Ejecutar de verdad, tras autorizacion escrita del dueno del entorno
  powershell -ExecutionPolicy Bypass -File database/saas/93_extraer_esquema_publico_5bq.ps1 `
      -Servidor "db.<proyecto>.supabase.co" -Puerto 5432 -BaseDatos "postgres" `
      -Usuario "postgres" `
      -CarpetaSalida "$env:USERPROFILE\Documents\fashionstore-esquema-5bq" -Autorizado

SALIDAS
  esquema_public_<sello>.sql        pg_dump --schema-only del/los esquema(s)
  catalogo_<sello>.json             contrato exacto de 87 (via 92)
  catalogo_texto_<sello>.txt        catalogo legible (via 81), opcional
  inventario_<sello>.json           rutas, bytes, sha256 y versiones
  validacion_<sello>.json           salida de 94 sobre dump + catalogo
#>
[CmdletBinding()]
param(
    [string]$Servidor,
    [int]$Puerto = 5432,
    [string]$BaseDatos = "postgres",
    [string]$Usuario,
    [string]$CarpetaSalida,
    [string]$Esquemas = "public",
    [string]$EsquemasExtra = "",
    [string]$PgBin,
    [string]$PythonExe = "python",
    [switch]$Autorizado,
    [switch]$SoloPlan,
    [switch]$SinCatalogoTexto,
    [switch]$ConservarTextoCatalogo
)

$ErrorActionPreference = "Stop"
$salida = $CarpetaSalida
$nombresEsquemas = @($Esquemas -split "," | ForEach-Object { $_.Trim() } | Where-Object { $_ })

# ---------------------------------------------------------------------------
# Guardas (se evalúan tambien en -SoloPlan)
# ---------------------------------------------------------------------------
if (-not $Autorizado -and -not $SoloPlan) {
    throw "Rechazado: falta -Autorizado. Solo lo ejecuta el estudiante con autorizacion escrita de lectura."
}
if ([string]::IsNullOrWhiteSpace($Servidor)) {
    throw "Rechazado: falta -Servidor (host de la conexion autorizada)."
}
if ($Servidor -in @("127.0.0.1", "localhost", "::1", "[::1]")) {
    throw "Rechazado: loopback. Los laboratorios locales usan 90_laboratorio_pg17_5bp.py."
}
if ($nombresEsquemas.Count -eq 0) {
    throw "Rechazado: -Esquemas quedo vacio. Indique al menos 'public'."
}
if ($nombresEsquemas -contains "auth" -or $nombresEsquemas -contains "storage") {
    throw "Rechazado: no se duplican schemas reservados de Supabase (auth/storage). Inventariar, no copiar."
}
if ([string]::IsNullOrWhiteSpace($salida)) {
    throw "Rechazado: falta -CarpetaSalida (debe quedar FUERA del repositorio)."
}

function Escribir-Utf8SinBom {
    # PowerShell 5.1 'Out-File -Encoding utf8' escribe BOM y 87 lee con
    # encoding="utf-8": un BOM lo hace fallar con JSONDecodeError. Este helper
    # escribe UTF-8 sin BOM, que es lo que exige el contrato de 87.
    param([Parameter(Mandatory = $true)][string]$Ruta,
          [Parameter(Mandatory = $true)][AllowEmptyString()][string]$Contenido)
    $utf8 = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($Ruta, $Contenido, $utf8)
}

$repo = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$salidaAbs = [System.IO.Path]::GetFullPath($salida)
$repoAbs = [System.IO.Path]::GetFullPath($repo)
if ($salidaAbs.StartsWith($repoAbs, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Rechazado: la salida '$salidaAbs' esta dentro del repositorio '$repoAbs'. Un dump nunca se versiona."
}

if ([string]::IsNullOrWhiteSpace($PgBin)) {
    $candidatos = @(
        (Join-Path $env:LOCALAPPDATA "Temp\opencode\fs_pg17_5bp\pg17\pgsql\bin"),
        "C:\Program Files\PostgreSQL\17\bin"
    )
    $PgBin = $candidatos | Where-Object {
        Test-Path (Join-Path $_ "pg_dump.exe")
    } | Select-Object -First 1
}
if ([string]::IsNullOrWhiteSpace($PgBin) -or -not (Test-Path (Join-Path $PgBin "pg_dump.exe"))) {
    throw "No se encontro pg_dump.exe. Ajuste -PgBin a una instalacion PostgreSQL 17.x."
}

$versionDump = (& (Join-Path $PgBin "pg_dump.exe") --version) 2>&1
if ($versionDump -notmatch "pg_dump \(PostgreSQL\) 17\.") {
    throw "Rechazado: se exige pg_dump 17.x y se encontro '$versionDump'. El origen verificado es PostgreSQL 17.6."
}
if (-not (Test-Path (Join-Path $PgBin "psql.exe"))) {
    throw "No se encontro psql.exe junto a pg_dump.exe en $PgBin."
}

$sqlCatalogoJson = Join-Path $PSScriptRoot "92_catalogo_json_solo_lectura_5bq.sql"
$sqlCatalogoTexto = Join-Path $PSScriptRoot "81_metadatos_catalogo_solo_lectura_5bo.sql"
$validador = Join-Path $PSScriptRoot "94_validar_insumo_esquema_5bq.py"
if (-not (Test-Path $sqlCatalogoJson)) { throw "Falta $sqlCatalogoJson" }
if (-not (Test-Path $validador)) { throw "Falta $validador" }

$marca = Get-Date -Format "yyyyMMdd_HHmmss"
$rutaDump = Join-Path $salidaAbs "esquema_public_$marca.sql"
$rutaJson = Join-Path $salidaAbs "catalogo_$marca.json"
$rutaTexto = Join-Path $salidaAbs "catalogo_texto_$marca.txt"
$rutaInventario = Join-Path $salidaAbs "inventario_$marca.json"
$rutaValidacion = Join-Path $salidaAbs "validacion_$marca.json"

$argumentosEsquema = @()
foreach ($esquema in $nombresEsquemas) { $argumentosEsquema += @("--schema=$esquema") }
foreach ($esquema in @($EsquemasExtra -split "," | ForEach-Object { $_.Trim() } | Where-Object { $_ })) {
    $argumentosEsquema += @("--schema=$esquema")
}

Write-Host "=== 5BQ extraccion autorizada de esquema ==="
Write-Host "Origen          : $Servidor`:$Puerto/$BaseDatos (usuario $Usuario)"
Write-Host "Esquemas        : $($nombresEsquemas -join ', ')"
Write-Host "pg_dump         : $versionDump"
Write-Host "Salida (fuera de Git): $salidaAbs"
Write-Host "Modo            : $(if ($SoloPlan) { 'PLAN (sin conexion)' } else { 'EJECUCION' })"
Write-Host ""

if ($SoloPlan) {
    Write-Host "--- Comandos que se ejecutarian ---"
    Write-Host ("pg_dump --schema-only --no-publications --no-subscriptions --encoding=UTF8 " +
        ($argumentosEsquema -join " ") +
        " --host <servidor> --port $Puerto --username $Usuario --dbname $BaseDatos --file `"$rutaDump`"")
    Write-Host ("psql --no-psqlrc --tuples-only --no-align --host <servidor> --port $Puerto " +
        "--username $Usuario --dbname $BaseDatos --file `"$sqlCatalogoJson`" > `"$rutaJson`"")
    if (-not $SinCatalogoTexto) {
        Write-Host ("psql --no-psqlrc --tuples-only --no-align --host <servidor> --port $Puerto " +
            "--username $Usuario --dbname $BaseDatos --file `"$sqlCatalogoTexto`" > `"$rutaTexto`"")
    }
    Write-Host ("& $PythonExe `"$validador`" --dump `"$rutaDump`" --catalogo `"$rutaJson`" " +
        "--json --salida `"$rutaValidacion`"")
    Write-Host ""
    Write-Host "--- Guardas verificadas ---"
    Write-Host "OK  -Autorizado exigido para ejecutar (no para planificar)"
    Write-Host "OK  host no es loopback"
    Write-Host "OK  salida fuera de $repoAbs"
    Write-Host "OK  pg_dump es 17.x"
    Write-Host "OK  solo SELECT de catalogo + pg_dump --schema-only (cero DDL/DML)"
    Write-Host "OK  la contrasena se pide de forma interactiva y no se escribe en disco"
    return
}

New-Item -ItemType Directory -Force -Path $salidaAbs | Out-Null

$env:PGSSLMODE = "require"
$env:PGOPTIONS = "-c statement_timeout=120000"
$seguro = Read-Host -AsSecureString "Contrasena del rol de lectura (no se muestra ni se guarda)"
$puntero = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($seguro)
try {
    $env:PGPASSWORD = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($puntero)

    Write-Host "1/4 pg_dump --schema-only ..."
    $argumentosDump = @(
        "--schema-only", "--no-publications", "--no-subscriptions", "--encoding=UTF8",
        "--host", $Servidor, "--port", "$Puerto", "--username", $Usuario,
        "--dbname", $BaseDatos, "--file", $rutaDump
    ) + $argumentosEsquema
    & (Join-Path $PgBin "pg_dump.exe") @argumentosDump
    if ($LASTEXITCODE -ne 0) { throw "pg_dump fallo con codigo $LASTEXITCODE" }

    Write-Host "2/4 catalogo JSON (92) ..."
    $jsonCrudo = & (Join-Path $PgBin "psql.exe") `
        --host $Servidor --port "$Puerto" --username $Usuario --dbname $BaseDatos `
        --no-psqlrc --tuples-only --no-align --file $sqlCatalogoJson
    if ($LASTEXITCODE -ne 0) { throw "psql (catalogo JSON) fallo con codigo $LASTEXITCODE" }
    if ([string]::IsNullOrWhiteSpace($jsonCrudo)) {
        throw "El catalogo JSON salio vacio: revise si 92 se ejecuto completo."
    }
    Escribir-Utf8SinBom -Ruta $rutaJson -Contenido (($jsonCrudo -join "`n") + "`n")

    if (-not $SinCatalogoTexto) {
        Write-Host "3/4 catalogo de texto (81) ..."
        $textoCrudo = & (Join-Path $PgBin "psql.exe") `
            --host $Servidor --port "$Puerto" --username $Usuario --dbname $BaseDatos `
            --no-psqlrc --file $sqlCatalogoTexto
        if ($LASTEXITCODE -ne 0) { throw "psql (catalogo texto) fallo con codigo $LASTEXITCODE" }
        Escribir-Utf8SinBom -Ruta $rutaTexto -Contenido (($textoCrudo -join "`r`n") + "`r`n")
    }
}
finally {
    Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue
    Remove-Item Env:PGOPTIONS -ErrorAction SilentlyContinue
    Remove-Item Env:PGSSLMODE -ErrorAction SilentlyContinue
    if ($puntero -ne [IntPtr]::Zero) {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($puntero)
    }
    $seguro = $null
}

Write-Host "4/4 validacion offline (94) ..."
$codigoValidacion = 0
& $PythonExe $validador --dump $rutaDump --catalogo $rutaJson `
    --json --salida $rutaValidacion | Out-Host
$codigoValidacion = $LASTEXITCODE

$dump = Get-Item $rutaDump
$json = Get-Item $rutaJson
$inventarioTexto = [ordered]@{
    fecha_utc              = (Get-Date).ToUniversalTime().ToString("o")
    etapa                  = "5BQ / Etapa H"
    origen                 = "$Servidor`:$Puerto/$BaseDatos"
    esquemas               = $nombresEsquemas
    version_pg_dump        = "$versionDump"
    esquema_sql            = $rutaDump
    esquema_bytes          = $dump.Length
    esquema_sha256         = (Get-FileHash -Algorithm SHA256 -Path $rutaDump).Hash.ToLower()
    catalogo_json          = $rutaJson
    catalogo_bytes         = $json.Length
    catalogo_sha256        = (Get-FileHash -Algorithm SHA256 -Path $rutaJson).Hash.ToLower()
    catalogo_texto         = $(if (Test-Path $rutaTexto) { $rutaTexto } else { $null })
    validacion             = $rutaValidacion
    codigo_validacion      = $codigoValidacion
    advertencias           = @(
        "Estos archivos NO se versionan ni se comparten con servicios externos.",
        "Un dump schema-only puede contener nombres de roles y comentarios: redactar antes de compartir.",
        "Un catalogo no es un respaldo ni un ensayo de migracion.",
        "Si el esquema public depende de objetos en 'extensions', el dump no los incluye: inventariar y crear en el laboratorio."
    )
} | ConvertTo-Json -Depth 4
Escribir-Utf8SinBom -Ruta $rutaInventario -Contenido ($inventarioTexto + "`n")

Write-Host ""
Write-Host "Inventario: $rutaInventario"
Write-Host "Codigo de validacion (94): $codigoValidacion  (0=apto, 4=discrepancias, 5=no apto)"
if ($codigoValidacion -eq 5) {
    Write-Host "INSUMO NO APTO: no continuar con 87 hasta corregirlo." -ForegroundColor Yellow
}
exit $codigoValidacion
