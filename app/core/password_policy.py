"""Politica de contrasenas compartida (autoridad: backend).

Modulo pequeno y reutilizable por:
- registro publico de cuenta de CLIENTE (POST /auth/clientes/registro);
- futuras tareas de restablecimiento/cambio de contrasena.

Reglas exactas (mismas que deben reflejar Web y Flutter en su barra de
seguridad; solo el estado VERDE permite enviar el formulario y el backend
valida otra vez):

1. Longitud entre 8 y 128 caracteres.
2. Al menos una letra minuscula (a-z).
3. Al menos una letra mayuscula (A-Z).
4. Al menos un digito (0-9).
5. Al menos un caracter especial: cualquier caracter que NO sea A-Z, a-z,
   0-9 ni espacio. Un espacio NO cuenta como especial.

La barra visual se calcula en Web/Flutter con estas mismas reglas:
ROJO = cumple 0-1 requisitos; AMARILLO = cumple parcialmente;
VERDE = cumple los 5 requisitos.

Este modulo NO valida contra la base de datos ni loguea la contrasena.
"""

LONGITUD_MINIMA = 8
LONGITUD_MAXIMA = 128

MENSAJE_POLITICA = (
    "La contraseña debe tener entre 8 y 128 caracteres e incluir una "
    "mayúscula, una minúscula, un número y un carácter especial."
)

#: Requisitos en orden, con la clave que puede usar el frontend en la barra.
REQUISITOS_PASSWORD = (
    ("longitud", f"Entre {LONGITUD_MINIMA} y {LONGITUD_MAXIMA} caracteres"),
    ("minuscula", "Al menos una letra minúscula"),
    ("mayuscula", "Al menos una letra mayúscula"),
    ("numero", "Al menos un número"),
    ("especial", "Al menos un carácter especial (! @ # $ % ...)"),
)


def _es_letra_ascii(caracter: str) -> bool:
    return ("a" <= caracter <= "z") or ("A" <= caracter <= "Z")


def _es_digito_ascii(caracter: str) -> bool:
    return "0" <= caracter <= "9"


def _es_especial(caracter: str) -> bool:
    """Especial = no es A-Z, a-z, 0-9 ni espacio."""
    return not (
        _es_letra_ascii(caracter)
        or _es_digito_ascii(caracter)
        or caracter == " "
    )


def evaluar_password(password: str) -> dict[str, bool]:
    """Estado de cada requisito (util para la barra de seguridad)."""
    texto = password if isinstance(password, str) else ""
    return {
        "longitud": LONGITUD_MINIMA <= len(texto) <= LONGITUD_MAXIMA,
        "minuscula": any(caracter.islower() for caracter in texto),
        "mayuscula": any(caracter.isupper() for caracter in texto),
        "numero": any(caracter.isdigit() for caracter in texto),
        "especial": any(_es_especial(caracter) for caracter in texto),
    }


def password_cumple_politica(password: str) -> bool:
    return all(evaluar_password(password).values())


def validar_password_segura(password: str) -> None:
    """Lanza ``ValueError`` con mensaje controlado si no cumple la politica."""
    if not password_cumple_politica(password):
        raise ValueError(MENSAJE_POLITICA)
