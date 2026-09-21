# CU22 - Stripe local (Test Mode) con Stripe CLI

Configuracion **local** para que `POST /pagos/stripe/intencion` y el webhook
`POST /pagos/stripe/webhook` funcionen en la maquina del desarrollador.

> Regla: este documento NO contiene claves reales. Nunca se versionan
> `sk_test_...`, `whsec_...` ni ningun secreto. El `.env` esta en `.gitignore`.

## 1. Dependencias

- `stripe==15.6.1` ya figura en `requirements.txt` (backend).
- Stripe CLI instalada y disponible en el PATH (`stripe --version`).

## 2. Backend: `.env` local

En `fashionstore-api/.env` (no versionado):

```env
STRIPE_SECRET_KEY=<SK_TEST_REAL_DE_ESTA_PC>
STRIPE_WEBHOOK_SECRET=<WHSEC_GENERADO_POR_STRIPE_LISTEN_EN_ESTA_PC>
STRIPE_CURRENCY=bob
```

`app/core/config.py` ya lee esos nombres (`stripe_secret_key`,
`stripe_webhook_secret`, `stripe_currency`) con `case_sensitive=False`; no hay
que tocar codigo. Si alguna variable falta, el provider lanza
`StripeConfiguracionError` y el endpoint responde
`500 {"detail": "Stripe no esta configurado en el servidor"}`.

## 3. Stripe CLI (cada desarrollador genera SU propio whsec)

Terminal 1:

```powershell
stripe login
```

Luego:

```powershell
$env:STRIPE_SECRET_KEY="<SK_TEST_REAL>"
stripe listen --forward-to http://127.0.0.1:8000/pagos/stripe/webhook
```

La CLI imprime:

```text
Ready! Your webhook signing secret is whsec_...
```

Ese `whsec_...` es **de esta PC** (no reutilizar el de otra maquina) y se pega
en `fashionstore-api/.env`:

```env
STRIPE_WEBHOOK_SECRET=whsec_...
```

## 4. Reinicio del backend

Las settings estan cacheadas (`@lru_cache` en `app/core/config.py`), asi que
`--reload` **no** basta: hay que detener uvicorn por completo y volver a
levantarlo.

```powershell
uvicorn app.main:app --reload
```

## 5. Verificacion (solo booleanos, sin imprimir secretos)

```powershell
python -c "from app.core.config import settings; print('secret:', bool(settings.stripe_secret_key)); print('webhook:', bool(settings.stripe_webhook_secret)); print('currency:', settings.stripe_currency)"
```

Esperado:

```text
secret: True
webhook: True
currency: bob
```

## 6. Frontend

El frontend solo puede recibir la **publishable key** (`pk_test_...`) por su
mecanismo de environment/config. Nunca `sk_test_...` ni `whsec_...`.

## 7. Flujo de prueba manual

1. Backend arriba (`uvicorn`).
2. `stripe listen ...` activo (Terminal 1).
3. Frontend arriba y sesion iniciada como CLIENTE.
4. Carrito -> pagar -> confirmar compra -> continuar al pago.
5. `POST /pagos/stripe/intencion` (requiere `venta_id` de una venta
   **WEB/MOVIL** en estado **PENDIENTE** del propio cliente).

Interpretacion de codigos:

| Codigo | Significado |
| --- | --- |
| 201 | PaymentIntent creado/reutilizado (correcto) |
| 500 `Stripe no esta configurado en el servidor` | Falta `STRIPE_SECRET_KEY` en `.env` o falta reiniciar uvicorn |
| 502 `La pasarela de pago no esta disponible` | La peticion llego a Stripe y fue rechazada (clave invalida, modo test/live cruzado, moneda no soportada). Revisar la respuesta real de Stripe antes de tocar codigo |
| 409 | Venta no es WEB/MOVIL, no esta PENDIENTE, sin detalle, ya aprobada, o monto/moneda invalidos |
| 404 / 403 | Venta inexistente / de otro cliente |
| 422 | Falta `venta_id` |

Si aparece 502, revisar primero el dashboard/logs de Stripe (moneda `bob` y
claves). No modificar codigo para "saltarse" ese error.
