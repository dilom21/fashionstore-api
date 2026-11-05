"""5BU / Etapa L - Pruebas adversariales offline del cifrado AEAD v2 de B2.

No abre PostgreSQL, no toca laboratorios y no usa datos reales: fabrica bytes
sinteticos en `tmp_path`. Verifica que toda manipulacion se detecte **antes**
de publicar plaintext (que es la condicion previa a un `pg_restore`).
"""
from __future__ import annotations

import importlib.util
import json
import struct
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[1]
MODULO = RAIZ / "database" / "saas" / "110_cifrado_autenticado_b2_5bu.py"


def _cargar():
    spec = importlib.util.spec_from_file_location("cifrado_b2_5bu", MODULO)
    modulo = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(modulo)
    return modulo


aead = _cargar()

FRASE = "frase-ficticia-de-prueba-5bu-2026"
FRASE_MALA = "frase-ficticia-incorrecta-5bu-2026"
MAGIC = b"FSB2AEAD"
PREFIJO = 8 + 1 + 1 + 4


def _dump_sintetico(ruta: Path, semilla: int = 7) -> bytes:
    datos = bytes((i * 31 + semilla) % 251 for i in range(20_000))
    ruta.write_bytes(datos)
    return datos


def _partes(blob: bytes) -> tuple[bytes, dict, bytes]:
    assert blob[:8] == MAGIC
    largo = struct.unpack(">I", blob[10:14])[0]
    cab = json.loads(blob[14:14 + largo].decode("utf-8"))
    resto = blob[14 + largo:]
    return blob[:14 + largo], cab, resto


def _reensamblar(prefijo: bytes, cab: dict, resto: bytes) -> bytes:
    crudo = aead._json_canonico(cab)
    nuevo_prefijo = (MAGIC + bytes([2, 1]) + struct.pack(">I", len(crudo)) + crudo)
    return nuevo_prefijo + resto


@pytest.fixture()
def paquete(tmp_path):
    plano = tmp_path / "base.dump"
    _dump_sintetico(plano)
    cifrado = tmp_path / "base.dump.fsb2"
    info = aead.cifrar_archivo(plano, cifrado, FRASE)
    return tmp_path, plano, cifrado, info


def test_roundtrip_y_formato_v2(paquete):
    tmp, plano, cifrado, info = paquete
    assert info["formato"] == "v2"
    assert info["suite"] == "AES-256-GCM"
    assert info["iteraciones"] == aead.ITERACIONES_DEFECTO
    verificado = aead.verificar(cifrado, FRASE)
    assert verificado["cabecera"]["suite"] == "AES-256-GCM"
    assert verificado["bytes_plano"] == plano.stat().st_size
    assert verificado["sha256_plano"] == info["sha256_plano"]

    salida = tmp / "restaurado.dump"
    aead.descifrar(cifrado, salida, FRASE)
    assert salida.read_bytes() == plano.read_bytes()


def test_frase_incorrecta_rechazada_sin_publicar_plaintext(paquete):
    tmp, _plano, cifrado, _info = paquete
    with pytest.raises(aead.AutenticacionFallida):
        aead.verificar(cifrado, FRASE_MALA)
    salida = tmp / "no_debe_existir.dump"
    with pytest.raises(aead.AutenticacionFallida):
        aead.descifrar(cifrado, salida, FRASE_MALA)
    assert not salida.exists()
    assert not list(tmp.glob("*.tmp"))


def test_bit_alterado_en_ciphertext(paquete, tmp_path):
    tmp, _plano, cifrado, _info = paquete
    blob = bytearray(cifrado.read_bytes())
    posicion = len(blob) - 100  # dentro del ciphertext (antes del tag de 16 bytes)
    blob[posicion] ^= 0x01
    alterado = tmp / "alterado_ct.fsb2"
    alterado.write_bytes(bytes(blob))
    with pytest.raises(aead.AutenticacionFallida):
        aead.verificar(alterado, FRASE)


def test_bit_alterado_en_tag(paquete):
    tmp, _plano, cifrado, _info = paquete
    blob = bytearray(cifrado.read_bytes())
    blob[-1] ^= 0x80
    alterado = tmp / "alterado_tag.fsb2"
    alterado.write_bytes(bytes(blob))
    with pytest.raises(aead.AutenticacionFallida):
        aead.verificar(alterado, FRASE)


def test_cabecera_manipulada_iteraciones(paquete):
    tmp, _plano, cifrado, _info = paquete
    _prefijo, cab, resto = _partes(cifrado.read_bytes())
    cab["iteraciones"] = 200_001
    manipulado = tmp / "manipulado_iter.fsb2"
    manipulado.write_bytes(_reensamblar(_prefijo, cab, resto))
    with pytest.raises(aead.AutenticacionFallida):
        aead.verificar(manipulado, FRASE)


def test_intercambio_de_cabeceras(paquete, tmp_path):
    tmp, _plano, cifrado, _info = paquete
    otra_plano = tmp / "otro.dump"
    otra_plano.write_bytes(b"contenido distinto para intercambio de cabecera" * 50)
    otro_cifrado = tmp / "otro.dump.fsb2"
    aead.cifrar_archivo(otra_plano, otro_cifrado, FRASE)

    prefijo_a, _cab_a, _resto_a = _partes(cifrado.read_bytes())
    _prefijo_b, _cab_b, resto_b = _partes(otro_cifrado.read_bytes())
    hibrido = tmp / "hibrido.fsb2"
    hibrido.write_bytes(prefijo_a + resto_b)
    with pytest.raises(aead.AutenticacionFallida):
        aead.verificar(hibrido, FRASE)


def test_archivo_truncado(paquete):
    tmp, _plano, cifrado, _info = paquete
    blob = cifrado.read_bytes()
    truncado = tmp / "truncado.fsb2"
    truncado.write_bytes(blob[:-10])
    with pytest.raises(aead.AutenticacionFallida):
        aead.verificar(truncado, FRASE)


def test_formato_legado_rechazado_sin_fallback(tmp_path):
    legado = tmp_path / "viejo.dump.enc"
    legado.write_bytes(b"Salted__" + b"\x00" * 64)
    with pytest.raises(aead.FormatoNoSoportado) as exc:
        aead.verificar(legado, FRASE)
    assert "legado" in str(exc.value).lower()
    assert "fallback" in str(exc.value).lower()


def test_firma_desconocida_rechazada(tmp_path):
    basura = tmp_path / "basura.bin"
    basura.write_bytes(b"NOPE" + b"\x00" * 40)
    with pytest.raises(aead.FormatoNoSoportado):
        aead.verificar(basura, FRASE)


def test_no_sobrescribe_salida(paquete):
    tmp, plano, _cifrado, _info = paquete
    existente = tmp / "existente.fsb2"
    existente.write_bytes(b"ocupado")
    with pytest.raises(aead.DestinoExistente):
        aead.cifrar_archivo(plano, existente, FRASE)


def test_nonce_y_salt_unicos_por_archivo(tmp_path):
    plano = tmp_path / "base.dump"
    plano.write_bytes(b"mismo contenido" * 1000)
    uno = tmp_path / "uno.fsb2"
    dos = tmp_path / "dos.fsb2"
    aead.cifrar_archivo(plano, uno, FRASE)
    aead.cifrar_archivo(plano, dos, FRASE)
    _p1, cab1, _r1 = _partes(uno.read_bytes())
    _p2, cab2, _r2 = _partes(dos.read_bytes())
    assert cab1["nonce_b64"] != cab2["nonce_b64"]
    assert cab1["salt_b64"] != cab2["salt_b64"]
    assert uno.read_bytes() != dos.read_bytes()


def test_frase_corta_rechazada(tmp_path):
    plano = tmp_path / "base.dump"
    plano.write_bytes(b"x" * 100)
    with pytest.raises(aead.CifradoError):
        aead.cifrar_archivo(plano, tmp_path / "corto.fsb2", "corta")


def test_iteraciones_bajo_minimo_rechazadas(tmp_path):
    plano = tmp_path / "base.dump"
    plano.write_bytes(b"x" * 100)
    with pytest.raises(aead.CifradoError):
        aead.cifrar_archivo(plano, tmp_path / "debil.fsb2", FRASE,
                            iteraciones=1000)


def test_verificar_no_escribe_archivos(paquete):
    tmp, _plano, cifrado, _info = paquete
    antes = {p.name for p in tmp.iterdir()}
    aead.verificar(cifrado, FRASE)
    despues = {p.name for p in tmp.iterdir()}
    assert antes == despues


def _autorizacion_rellena(ruta: Path) -> Path:
    ruta.write_text(
        "AUTORIZACION B2\nAutorizo / No autorizo: Si, autorizo\n"
        "Alcance autorizado: datos + estructura\nCustodio del respaldo: prueba\n"
        "TTL del respaldo: 72\nFirma / fecha: prueba 2026-10-31\n",
        encoding="utf-8")
    return ruta


def _puerto_efimero() -> int:
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_preflight_bloquea_sin_autorizacion(tmp_path):
    aut = tmp_path / "104.md"
    aut.write_text(
        "AUTORIZACION B2\nAutorizo / No autorizo: <texto>\n"
        "Alcance autorizado: <datos + estructura | solo estructura>\n"
        "Custodio del respaldo: <nombre>\nTTL del respaldo: <horas>\n"
        "Firma / fecha: <...>\n", encoding="utf-8")
    res = aead.preflight_preb2(
        origen_host="db.ejemplo.supabase.co", origen_puerto=5432,
        destino=tmp_path / "destino", puerto_restore=_puerto_efimero(),
        autorizacion=aut)
    assert res["ok"] is False
    assert any("incompleta" in b for b in res["bloqueos"])


def test_preflight_bloquea_origen_de_laboratorio(tmp_path):
    aut = _autorizacion_rellena(tmp_path / "104.md")
    res = aead.preflight_preb2(
        origen_host="127.0.0.1", origen_puerto=55452,
        destino=tmp_path / "destino", puerto_restore=55453,
        autorizacion=aut)
    assert res["ok"] is False
    assert any("laboratorio local" in b for b in res["bloqueos"])


def test_preflight_acepta_origen_5432_con_autorizacion(tmp_path):
    aut = _autorizacion_rellena(tmp_path / "104.md")
    res = aead.preflight_preb2(
        origen_host="db.ejemplo.supabase.co", origen_puerto=5432,
        destino=tmp_path / "destino", puerto_restore=_puerto_efimero(),
        autorizacion=aut)
    assert res["ok"] is True
