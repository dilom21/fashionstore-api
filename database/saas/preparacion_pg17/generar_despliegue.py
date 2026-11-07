"""Genera las copias de despliegue de seguridad sin la guarda NO_EJECUTAR.

Los archivos ``*_PROPUESTA_NO_EJECUTAR.sql`` siguen protegidos: este
generador NUNCA los modifica. Escribe en ``despliegue/`` una copia por
propuesta con la guarda ``BLOQUEADO`` retirada, el sha256 del original
anotado en la cabecera y un ``MANIFIESTO.sha256`` para auditar la ventana.

Reglas (mismo espíritu que el generador del laboratorio 5CB):

* Si el original no contiene EXACTAMENTE una guarda, aborta. Un original ya
  desprotegido no debe producir copias desplegables: la protección es la que
  separa «propuesta revisada» de «archivo autorizado».
* La copia se obtiene quitando sólo la guarda. Si el resultado difiere del
  original por cualquier otra razón, es un error del generador.
* Es idempotente: si la copia ya existe con el mismo contenido no se reescribe
  y el manifiesto se regenera igual.

No se leen ni escriben credenciales: el generador trabaja sólo con el texto de
los SQL versionados.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
SALIDA = HERE / "despliegue"

# Propuesta -> (archivo original, nombre de la copia de despliegue).
PROPUESTAS: dict[str, tuple[str, str]] = {
    "050a": (
        "050a_identidad_jwt_roles_PROPUESTA_NO_EJECUTAR.sql",
        "050a_identidad_jwt_roles.sql",
    ),
    "050b": (
        "050b_rls_policies_PROPUESTA_NO_EJECUTAR.sql",
        "050b_rls_policies.sql",
    ),
    "050c": (
        "050c_acl_procedimientos_PROPUESTA_NO_EJECUTAR.sql",
        "050c_acl_procedimientos.sql",
    ),
    "055": (
        "055_retirar_compatibilidad_legacy_PROPUESTA_NO_EJECUTAR.sql",
        "055_retirar_compatibilidad_legacy.sql",
    ),
}

# 050a usa la guarda en tres líneas y 050b/050c en una sola; el patrón cubre
# ambos formatos y consume el salto de línea final de la guarda.
PATRON_GUARDA = re.compile(
    r"DO \$\$ BEGIN\s+RAISE EXCEPTION 'BLOQUEADO[^']*';\s+END \$\$;[ \t]*\n"
)


class ErrorDespliegue(RuntimeError):
    """Fallo deliberado: el despliegue no debe continuar."""


def huella(texto: str) -> str:
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()


def leer(ruta: Path) -> str:
    """Lee normalizando finales de línea para que el sha256 sea reproducible."""
    with open(ruta, "r", encoding="utf-8", newline="") as f:
        return f.read().replace("\r\n", "\n")


def escribir(ruta: Path, texto: str) -> None:
    with open(ruta, "w", encoding="utf-8", newline="\n") as f:
        f.write(texto)


def quitar_guarda(texto: str, etiqueta: str) -> tuple[str, int]:
    """Devuelve el texto sin la guarda y el número de guardas retiradas."""
    retiradas = len(PATRON_GUARDA.findall(texto))
    if retiradas == 0:
        raise ErrorDespliegue(
            f"{etiqueta}: no se encontró la guarda BLOQUEADO. El original está "
            "desprotegido o cambió de formato; revisar antes de generar copias."
        )
    if retiradas > 1:
        raise ErrorDespliegue(
            f"{etiqueta}: hay {retiradas} guardas BLOQUEADO; la estructura no es "
            "la esperada (una sola guarda al inicio)."
        )
    limpio = PATRON_GUARDA.sub("", texto, count=1)
    if limpio == texto:
        raise ErrorDespliegue(f"{etiqueta}: la guarda no pudo retirarse")
    return limpio, retiradas


def cabecera(etiqueta: str, original: str, sha_original: str) -> str:
    return (
        f"-- COPIA DE DESPLIEGUE generada por generar_despliegue.py — NO EDITAR A MANO.\n"
        f"-- Propuesta: {etiqueta}\n"
        f"-- Original protegido: {original}\n"
        f"-- sha256 del original: {sha_original}\n"
        f"-- Cambio aplicado: retirada de la guarda 'BLOQUEADO: propuesta ... no aprobada'\n"
        f"--   (una sola sentencia DO del encabezado). El resto del archivo es idéntico\n"
        f"--   byte a byte; el sha256 de esta copia queda en MANIFIESTO.sha256.\n"
        f"-- Esta copia no modifica el original ni constituye por sí misma una\n"
        f"-- autorización: la ventana de despliegue se aprueba aparte.\n"
        f"-- Nota: los comentarios 'NO EJECUTAR' del cuerpo son texto del original y\n"
        f"--   se conservan sin cambios para que la copia sea auditable.\n"
        f"\n"
    )


def main() -> int:
    SALIDA.mkdir(exist_ok=True)
    lineas_manifiesto: list[str] = [
        "# MANIFIESTO de despliegue Supabase — generado por generar_despliegue.py.",
        "# Formato: sha256_copia  sha256_original  archivo_copia  archivo_original",
        "# No editar a mano. Regenerar con: python generar_despliegue.py",
        "",
    ]
    generados = 0
    sin_cambios = 0

    for etiqueta, (nombre_original, nombre_copia) in PROPUESTAS.items():
        ruta_original = HERE / nombre_original
        if not ruta_original.is_file():
            raise ErrorDespliegue(f"falta el original {nombre_original}")
        texto = leer(ruta_original)
        sha_original = huella(texto)
        limpio, _ = quitar_guarda(texto, nombre_original)

        encabezado = cabecera(etiqueta, nombre_original, sha_original)
        contenido = encabezado + limpio
        # Garantía de «sólo cambia la guarda»: el cuerpo es el original menos la
        # guarda, y el original del que salió conserva su sha256 en la cabecera.
        if not contenido.endswith(limpio) or huella(limpio) == sha_original:
            raise ErrorDespliegue(f"{nombre_copia}: cuerpo inconsistente con el original")
        ruta_copia = SALIDA / nombre_copia
        if ruta_copia.is_file() and leer(ruta_copia) == contenido:
            sin_cambios += 1
            estado = "sin cambios"
        else:
            escribir(ruta_copia, contenido)
            generados += 1
            estado = "generado"

        lineas_manifiesto.append(
            f"{huella(contenido)}  {sha_original}  {nombre_copia}  {nombre_original}"
        )
        print(f"[{etiqueta}] {estado}: despliegue/{nombre_copia} (original {sha_original[:12]}…)")

    lineas_manifiesto.append("")
    escribir(SALIDA / "MANIFIESTO.sha256", "\n".join(lineas_manifiesto))
    print(f"copias generadas: {generados}; sin cambios: {sin_cambios}; manifiesto: despliegue/MANIFIESTO.sha256")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
