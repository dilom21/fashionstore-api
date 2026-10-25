"""5BH: inventariar fuentes para reproducir el esquema empresarial completo.

AUDITORÍA EXCLUSIVAMENTE ESTÁTICA: git ls-files y lectura de fuentes SQL
versionadas; nunca importa módulos de la app, no carga .env, no usa
psycopg, SQLAlchemy, psql, Docker ni inicia/detiene PostgreSQL.
NO establece conexiones a 5432, 55432, 55433 ni a Supabase.

Motivación: 5BG aprobó CU16/17/18 contra un esquema ficticio parcial
de 25 tablas de negocio, sin las 47 tablas ni 48 FK tenant y triggers
del esquema completo probado en el antiguo laboratorio 55432.
NO basta una coincidencia textual de CREATE TABLE para certificar
que las migraciones reconstruyan correctamente el sistema real.

Este inventario localiza candidatos versionados a: DDL de tablas,
FK, policies RLS, triggers y funciones. No ejecuta, migra ni copia
ninguno y no imprime texto del SQL (potencialmente contiene secretos).
Su reporte es orientativo: la compatibilidad/reproducibilidad exacta
requiere un clúster NUEVO en otro ensayo expresamente autorizado.
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path
import re
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[2]
MAX_BYTES=2_000_000
# Solo formatos fuente de SQL y generadores Python de DDL.
SQL_SUFFIXES=(".sql",".sql.txt",".psql",".pgsql",".ddl")
PY_SUFFIX=".py"
EXCLUDE_PARTS={
    ".git",".venv","venv","env","__pycache__",".pytest_cache",
    "node_modules",".mypy_cache","dist","build",
}
PATTERNS={
    "tablas":re.compile(r"\bCREATE\s+TABLE\b",re.I),
    "fk":re.compile(r"\b(?:FOREIGN\s+KEY\b|REFERENCES\b|ADD\s+CONSTRAINT\b)",re.I),
    "rls":re.compile(r"\b(?:CREATE\s+POLICY\b|ENABLE\s+ROW\s+LEVEL\s+SECURITY\b)",re.I),
    "triggers":re.compile(r"\bCREATE\s+(?:OR\s+REPLACE\s+)?TRIGGER\b",re.I),
    "funciones":re.compile(r"\bCREATE\s+(?:OR\s+REPLACE\s+)?FUNCTION\b",re.I),
    "procedimientos":re.compile(r"\bCREATE\s+(?:OR\s+REPLACE\s+)?PROCEDURE\b",re.I),
}
REQUIRED_FAMILIES=("tablas","fk","rls","triggers")


def ensure(ok,message):
    if not ok:
        raise RuntimeError("5BH BLOQUEADO: "+message)


def categorizar(path):
    """Devolver 'saas-ensayo', 'resto-versionado' o 'no-fuente'."""
    parts=Path(path).parts
    if any(p.lower() in EXCLUDE_PARTS for p in parts):
        return "no-fuente"
    lower=path.lower().replace("\\","/")
    if not (lower.endswith(SQL_SUFFIXES) or lower.endswith(PY_SUFFIX)):
        return "no-fuente"
    if lower.startswith("database/saas/") or lower.startswith("tests_saas/"):
        return "saas-ensayo"
    return "resto-versionado"


def tracked_paths():
    ensure((ROOT/"database/saas"/
            "66_sp_comerciales_saas_stock_real_temporal_5bg.py").is_file(),
           "directorio raíz de FashionStore no encontrado")
    # Sólo operación local de git, SIN fetch, pull, push, red ni hooks.
    r=subprocess.run(["git","-C",str(ROOT),"ls-files","-z"],
                     stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                     timeout=20,check=False)
    ensure(r.returncode==0,"git ls-files no pudo listar archivos locales")
    rels=[Path(x.decode("utf-8",errors="replace"))
          for x in r.stdout.split(b"\0") if x]
    ensure(bool(rels),"repositorio no contiene archivos versionados")
    return rels


def scan_sources(paths):
    """Solo leer ficheros fuente versionados bajo la raíz, nunca secretos."""
    found=[]
    omitted=Counter()
    for rel in paths:
        name=rel.as_posix()
        category=categorizar(name)
        if category=="no-fuente":
            continue
        full=ROOT/rel
        # No seguir links fuera del checkout y no leer archivos grandes.
        if not full.is_file() or not full.resolve().is_relative_to(ROOT.resolve()):
            omitted["fuera_checkout_o_ausente"]+=1
            continue
        if full.stat().st_size>MAX_BYTES:
            omitted["muy_grande"]+=1
            continue
        try:
            source=full.read_text(encoding="utf-8",errors="replace")
        except OSError:
            omitted["no_legible"]+=1
            continue
        hits={key:len(pattern.findall(source))
              for key,pattern in PATTERNS.items()}
        if not any(hits.values()):
            continue
        found.append((name,category,hits))
    return found,omitted


def summary(found):
    totals={"saas-ensayo":Counter(),"resto-versionado":Counter()}
    for _,category,hits in found:
        totals[category].update(hits)
    return totals


def informe(found,omitted):
    totals=summary(found)
    print("5BH AUDITORÍA LOCAL PASS: git ls-files, sin conectar a bases",
          flush=True)
    print("5BH FUENTES: señales textuales por categoría (NO ejecutadas)")
    for category in ("resto-versionado","saas-ensayo"):
        cnt=sum(1 for _,c,_ in found if c==category)
        stats=totals[category]
        print("  "+category+": "+str(cnt)+" archivos SQL/Python con DDL; "+
              ", ".join(f"{k}={stats[k]}" for k in PATTERNS))
    print("5BH CANDIDATOS FUERA DE ENSAYOS:")
    outside=sorted((name,hits) for name,cat,hits in found
                   if cat=="resto-versionado")
    if not outside:
        print("  Ninguno detectado; falta identificar cómo reconstruir esquema")
    else:
        # Solo mostrar rutas relativas, no sentencias ni credenciales.
        for name,hits in outside[:45]:
            kinds=",".join(k for k,v in hits.items() if v)
            print("  "+name+" ["+kinds+"]")
        if len(outside)>45:
            print("  ... "+str(len(outside)-45)+" candidatos adicionales")
    if omitted:
        print("5BH OMITIDOS: "+", ".join(f"{k}={v}"
                                      for k,v in sorted(omitted.items())))
    missing=[k for k in REQUIRED_FAMILIES
             if totals["resto-versionado"][k]==0]
    if missing:
        print("5BH FALTA EVIDENCIA: sin fuente versionada fuera de ensayos "
              "para "+", ".join(missing))
    else:
        print("5BH POSIBLES FUENTES: hay señales de tablas/FK/RLS/triggers "
              "fuera de ensayos, pero NO están validadas")
    print("5BH PENDIENTE: demostrar reconstrucción de 47 tablas, "
          "48 FK, policies, triggers y fixtures comerciales FICTICIOS "
          "sobre un PostgreSQL 18 NUEVO y desechable")
    print("5BH SEGURIDAD: ninguna conexión SQL, flags SaaS OFF; "
          "PG 5432/55432/55433 no tocados")


def main():
    found,omitted=scan_sources(tracked_paths())
    informe(found,omitted)


if __name__=="__main__":
    try:
        main()
    except Exception as err:
        print(f"5BH ERROR: {type(err).__name__}: {err}",
              file=sys.stderr)
        sys.exit(2)
