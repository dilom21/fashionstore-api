"""5AE: verificaciones ESTATICAS de fixture SQL; sin conexiones ni DML."""
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
SQL=(ROOT/"database/saas/38_cargar_fixtures_comerciales_5ae.sql").read_text(encoding="utf-8")

def test_5ae_guardas_y_transaccion():
    assert "BEGIN;" in SQL and "COMMIT;" in SQL
    assert "fashionstore_rls_lab_5u" in SQL
    assert "inet_server_port() <> 55432" in SQL
    assert "data_directory" in SQL
    assert "session_user <> 'postgres'" in SQL

def test_5ae_integridad_verificada_previa():
    for needed in ("<> 46","<> 48","<> 35","count(*) FROM public.empresa"):
        assert needed in SQL
    assert "datos preexistentes" in SQL

def test_5ae_dos_tenants_ficticios():
    assert "'empresa-lab-a','empresa-lab-b'" in SQL
    assert "Cliente-Ficticio-" in SQL
    assert "Producto-Ficticio-" in SQL

def test_5ae_fk_y_dependencias():
    for table in ("ciudad","talla","color","cliente","sucursal","categoria",
                  "producto","temporada","variante_producto","inventario",
                  "carrito","detalle_carrito","reserva","detalle_reserva"):
        assert "public."+table in SQL
    assert "RETURNING id INTO" in SQL

def test_5ae_reserva_y_stock():
    assert "stock_actual,stock_reservado" in SQL
    assert "VALUES(v.id,v_sucursal,v_variante,v_temporada,5,0)" in SQL
    assert "'PENDIENTE'" in SQL
    assert "now()+ interval '1 day'" in SQL

def test_5ae_sin_reales_ni_destruccion():
    for danger in ("fashionstore_restore_", "supabase db", "pg_dump",
                   "DROP TABLE", "DROP ROLE", "TRUNCATE", "password_hash",
                   "INSERT INTO public.usuario"):
        assert danger not in SQL
