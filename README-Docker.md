# FashionStore API — entrega de dockerización a Harold

## Responsabilidades
Josias entrega imagen/Compose/Nginx y pruebas; Harold administra EC2, DuckDNS, certificados TLS, .env privado y operaciones. La base sigue siendo Supabase; no hay contenedor PostgreSQL.

## Ubicación exacta
Colocar `Dockerfile`, `.dockerignore`, `compose.yaml`, `.env.example` y `README-Docker.md` en la raíz del repositorio `fashionstore-api/` (junto a `requirements.txt`). Colocar `nginx/fashionstore.conf` dentro de `fashionstore-api/nginx/`.

En EC2, Harold configura el repositorio bajo `/opt/fashion-store/fashionstore-api` o la carpeta realmente acordada. No mezclar con el proyecto frontend.

## Preflight obligatorio
1. Revisar los seis archivos del PR. En el laboratorio local de OpenCode (11/11/2026) se verificaron `docker compose config --quiet`, build Python 3.12, Uvicorn sin `--reload`, `/health` HTTP 200 y reinicio. Eso NO sustituye la comprobación en EC2.
2. Verificar que `/health` de FastAPI no requiere credenciales y que `requirements.txt` instala sobre Python 3.12.
3. Certificado EXISTENTE en el HOST: `/etc/letsencrypt/live/fashionstore-api.duckdns.org/fullchain.pem` y `privkey.pem` antes de iniciar Nginx. Si Harold usa Nginx/Certbot en el host, adaptar `compose.yaml`: no ocupar simultáneamente puertos 80/443.
4. Crear `.env` SOLO en EC2 a partir de `.env.example`, con DSN restringidos separados para API/bootstrap/plataforma/webhook; cifrar o usar gestor de secretos. `chmod 600 .env`. Nunca copiar contraseñas al chat.
5. Configurar allowed origins para todos los dominios frontend realmente usados; backend debe permitir Origin del frontend Vercel. Verificar CORS de Flutter web si corresponde.
6. Mantener offline 106 desactivado hasta validar la migración en PostgreSQL 17 aislado. Las migraciones Supabase de sesiones y ACL ya fueron ejecutadas; no repetirlas.

## Primera emisión del certificado HTTPS en EC2 — Harold

**No iniciar el servicio nginx de Compose antes de tener certificados reales.** El archivo `nginx/fashionstore.conf` referencia `/etc/letsencrypt/live/fashionstore-api.duckdns.org/fullchain.pem` y `privkey.pem` y Nginx fallará mientras no existan. No crear archivos de certificado falsos.

1. Verificar DNS del dominio hacia la Elastic IP actual, Security Group y firewall: abrir TCP 80 para ACME HTTP-01 y 443 para HTTPS; SSH 22 solo desde IP autorizada. Nunca publicar 8000/5432. Confirmar que ni Nginx del host ni otro contenedor ocupan 80/443; elegir **un solo proxy**.
2. En EC2 Ubuntu, con el backend actualizado, el `.env` real protegido y después de `docker compose config --quiet`, construir y arrancar **solamente API**: `docker compose up -d --build api`. Revisar `docker compose ps api` y `docker compose logs --tail=80 api`.
3. Si se va a usar el Nginx **del contenedor**, instalar Certbot **en el host** con el gestor de paquetes y emitir una sola vez con `sudo certbot certonly --standalone -d fashionstore-api.duckdns.org` mientras el puerto 80 esté libre. Completar correo y términos mediante el procedimiento interactivo seguro de Certbot. Requiere que HTTP-01 llegue desde Internet a TCP 80; si no llega, **detenerse** y corregir DNS/SG/UFW, no usar `--test-cert` en producción.
4. Verificar que ambos archivos estén disponibles en `/etc/letsencrypt/live/fashionstore-api.duckdns.org/` y recién entonces ejecutar `docker compose up -d nginx`. Comprobar con `curl -fsS -i https://fashionstore-api.duckdns.org/health` **sin** `-k` y que `docker compose ps` indique API healthy y Nginx arriba.
5. **Renovación:** como el certificado fue emitido con `--standalone`, renovar necesita liberar TCP 80 durante HTTP-01. Harold debe instalar y probar hooks seguros para detener/reiniciar únicamente su proxy contenedor, o migrar a renovación `webroot`/DNS administrada. Ejecutar `certbot renew --dry-run` para demostrar renovación y documentar el procedimiento antes de dar por terminada la infraestructura. No asumir que `certbot renew` funcionará con Nginx ocupando 80.
6. En caso de que Harold ya tenga **Nginx de host** con TLS activo, NO usar el servicio nginx de Compose ni competir por 80/443: adaptar la topología conjuntamente, preservando el puerto 8000 privado y la limitación de intentos de login.

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

**Estado:** build y health Docker local reportados PASS por OpenCode; falta certificado HTTPS real y validación EC2 a cargo de Harold. No habilitar servicios SaaS sin los controles de autenticación, permisos y smoke remoto.
