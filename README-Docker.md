# FashionStore API — entrega de dockerización a Harold

## Responsabilidades
Josias entrega imagen/Compose/Nginx y pruebas; Harold administra EC2, DuckDNS, certificados TLS, .env privado y operaciones. La base sigue siendo Supabase; no hay contenedor PostgreSQL.

## Ubicación exacta
Colocar `Dockerfile`, `.dockerignore`, `compose.yaml`, `.env.example` y `README-Docker.md` en la raíz del repositorio `fashionstore-api/` (junto a `requirements.txt`). Colocar `nginx/fashionstore.conf` dentro de `fashionstore-api/nginx/`.

En EC2, Harold configura el repositorio bajo `/opt/fashion-store/fashionstore-api` o la carpeta realmente acordada. No mezclar con el proyecto frontend.

## Preflight obligatorio
1. Hacer revisión conjunta de estos archivos. No han sido construidos en este entorno porque Docker no está disponible aquí.
2. Verificar que `/health` de FastAPI no requiere credenciales y que `requirements.txt` instala sobre Python 3.12.
3. Certificado EXISTENTE en el HOST: `/etc/letsencrypt/live/fashionstore-api.duckdns.org/fullchain.pem` y `privkey.pem` antes de iniciar Nginx. Si Harold usa Nginx/Certbot en el host, adaptar `compose.yaml`: no ocupar simultáneamente puertos 80/443.
4. Crear `.env` SOLO en EC2 a partir de `.env.example`, con DSN restringidos separados para API/bootstrap/plataforma/webhook; cifrar o usar gestor de secretos. `chmod 600 .env`. Nunca copiar contraseñas al chat.
5. Configurar allowed origins para todos los dominios frontend realmente usados; backend debe permitir Origin del frontend Vercel. Verificar CORS de Flutter web si corresponde.
6. Mantener offline 106 desactivado hasta validar la migración en PostgreSQL 17 aislado. Las migraciones Supabase de sesiones y ACL ya fueron ejecutadas; no repetirlas.

## Comandos locales (en la raíz backend)
```bash
docker compose config --quiet
docker compose build api
# Solo cuando el certificado del host y el .env estén preparados:
docker compose up -d
docker compose ps
docker compose logs --tail=100 api nginx
curl -f https://fashionstore-api.duckdns.org/health
```

## Seguridad
- `api:8000` solo en la red interna; únicamente Nginx publica 80/443.
- Nginx limita `/saas/superadmin/auth/login` a 5 solicitudes/minuto por IP y devuelve 429 si excede; esto es protección de gateway en una sola EC2, no rate limiting global distribuido entre varias instancias. Coordinar un WAF externo si hay escalado.
- No ejecutar migraciones automáticamente al levantar contenedores.
- `SAAS_SUPERADMIN_COOKIE_SECURE=true` con HTTPS real. HttpOnly y CSRF son controlados por el backend.
- Probar login y logout usando el mismo origen BFF de Vercel; recordar redeploy tras cambiar variables SSR.

## Pruebas y rollback
Probar `/health`, `POST /saas/superadmin/auth/login` mediante BFF, `/auth/me`, CSRF, CRUD empresas/planes/suscripciones, tenant isolation, y Stripe webhook en modo prueba con firma. Confirmar reinicio Docker, logs y healthchecks. No probar ni modificar ventas reales como parte del smoke.

Rollback: conservar SHA anterior, etiqueta de imagen anterior y archivo Compose revisado. Volver a SHA/imagen verificada si falla el smoke, sin revertir migraciones compartidas de Supabase.

**Estado:** paquete de propuesta listo para revisión; no se ha ejecutado build Docker ni validación real EC2 en este entorno.
