-- PLANTILLA pgTAP/psql. NO EJECUTAR en bases existentes.
-- Requiere base desechable, dos empresas y tres tokens JWT válidos inyectados
-- como variables psql: token_a1, token_a2 (misma empresa), token_b1.
\if :{?token_a1}
\else
\quit 3
\endif
\if :{?token_a2}
\else
\quit 3
\endif
\if :{?token_b1}
\else
\quit 3
\endif

BEGIN;
SET LOCAL ROLE fashionstore_api;
SELECT set_config('app.actor_token',:'token_a1',true);

-- El runner debe convertir cada expectativa en lives_ok/throws_ok/results_eq.
-- A1: exactamente una empresa visible; cero filas de B en las 31 tablas.
SELECT count(*)=1 AS una_empresa_visible FROM public.empresa;
SELECT bool_and(empresa_id=fashionstore_private.actor_empresa_id()) FROM public.producto;

-- GUCs antiguos manipulados no cambian la identidad firmada.
SELECT set_config('app.empresa_id','9223372036854775807',true);
SELECT set_config('app.usuario_id','9223372036854775807',true);
SELECT fashionstore_private.actor_empresa_id(),fashionstore_private.actor_usuario_id();

-- Token A1 sustituido por A2: A1 no debe leer/modificar carrito/reserva de A2.
-- Token B1: SELECT/INSERT/UPDATE/DELETE con IDs de A deben devolver 0 o 42501.
-- Un token alterado, expirado, sin jti o con alg distinto debe producir 42501.
-- UPDATE que cambie empresa_id debe fallar por RLS/trigger.
-- CALL con p_usuario_id distinto del token debe producir 42501.
-- CALL con empresa distinta debe producir 42501.
-- API no puede ejecutar procedimientos legacy ni INSERT directo en bitácora.
ROLLBACK;

