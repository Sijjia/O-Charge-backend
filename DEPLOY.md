# O!Charge — деплой с нуля

Полная инструкция как развернуть O!Charge на чистом сервере (как было сделано на Orion 2026-05-02). Покрывает PWA + Backend (FastAPI/OCPP) + Self-hosted Supabase.

---

## 0. Архитектура

```
Caddy (host) :80/:443  ←  Let's Encrypt автомат
   │
   ├─ o.asystem.ai      → 127.0.0.1:14084 → ocharge-pwa (Vite SPA)
   ├─ ocpp.asystem.ai   → 127.0.0.1:18900 → evpower-backend (FastAPI+Redis, WSS OCPP)
   └─ supabase.asystem.ai → 127.0.0.1:18800 → supabase-kong → 14 контейнеров Supabase

Coolify (c.asystem.ai) — оркестратор, GitHub auto-deploy, env, логи
GitHub: Sijjia/O-Charge-pwa, Sijjia/O-Charge-backend
```

Прокси: **host-Caddy** (`/etc/caddy/conf.d/*.conf`), а НЕ Coolify-Traefik. Каждый домен = отдельный `.conf`.

---

## 1. Требования к серверу

- Linux (Ubuntu 22.04+ / Debian 12)
- ≥ 4 GB RAM, 50 GB SSD, 2+ CPU
- Открытые порты: `22` (SSH), `80`, `443`
- Cloudflare DNS-зона на нужный домен (можно любой регистратор, главное — авторитетный NS)
- Coolify уже установлен на отдельном хосте (или на этом же — см. шаг 2)

---

## 2. Установка Coolify (если ещё не стоит)

На сервере где будет панель Coolify:

```bash
curl -fsSL https://cdn.coollabs.io/coolify/install.sh | sudo bash
```

После установки:
- Открой `https://c.<твой-домен>`
- Создай админ-аккаунт
- В Settings → API Tokens — сгенерируй токен с `read+write`, сохрани

Если Coolify хостится на Hetzner: знай что они банят за phishing — proximus уже улетел.

---

## 3. Подключение целевого сервера к Coolify

В Coolify → Servers → New Server:
- IP / hostname рабочего сервера (Orion в нашем случае)
- SSH пользователь и приватный ключ (Coolify сам сгенерит публичный — добавь его в `~/.ssh/authorized_keys` целевого сервера)

После подключения у сервера появится UUID (например `m1047viwqty3nfnokp60egrc`).

---

## 4. DNS

В Cloudflare для зоны `<твой-домен>` создай A-записи:

| Имя | Type | Content | TTL | Proxy |
|-----|------|---------|-----|-------|
| `o.<домен>` | A | `<IP сервера>` | 60 | OFF |
| `ocpp.<домен>` | A | `<IP сервера>` | 60 | OFF |
| `supabase.<домен>` | A | `<IP сервера>` | 60 | OFF |

Proxy выключен — нужно для Let's Encrypt HTTP-01 и WSS OCPP. Если хочется CF-проксирование, придётся ставить Cloudflare Tunnel вместо прямой A-записи.

Через Cloudflare API (если есть токен):

```bash
CF_TOKEN=cfut_xxx
ZONE=<zone-id-из-CF-dashboard>
IP=<IP-сервера>

for SUB in o ocpp supabase; do
  curl -s -X POST -H "Authorization: Bearer $CF_TOKEN" -H "Content-Type: application/json" \
    "https://api.cloudflare.com/client/v4/zones/$ZONE/dns_records" \
    --data "{\"type\":\"A\",\"name\":\"${SUB}.<домен>\",\"content\":\"$IP\",\"ttl\":60,\"proxied\":false}"
done

# Проверка
dig +short o.<домен> @1.1.1.1
```

---

## 5. Caddy на целевом сервере

Caddy ставит и держит HTTPS на 80/443. Coolify-Traefik не используется (порт занят Caddy).

```bash
sudo apt install -y caddy
sudo systemctl enable --now caddy
```

`/etc/caddy/Caddyfile`:

```caddyfile
{
  email <твой-email-для-LE>
  servers {
    timeouts { read_body 10m read_header 30s write 10m idle 5m }
    max_header_size 16KB
  }
}

c.<домен> {
  encode gzip zstd
  header { Strict-Transport-Security "max-age=31536000; includeSubDomains; preload" }
  handle /app/* { reverse_proxy 127.0.0.1:6001 }
  handle /terminal/* { reverse_proxy 127.0.0.1:6002 }
  handle { reverse_proxy 127.0.0.1:8000 }
  log { output file /var/log/caddy/c.<домен>.log }
}

import /etc/caddy/conf.d/*.conf
```

Создай `/etc/caddy/conf.d/o.<домен>.conf`:

```caddyfile
o.<домен> {
  encode gzip zstd
  header {
    Strict-Transport-Security "max-age=31536000; includeSubDomains; preload"
    X-Content-Type-Options "nosniff"
  }
  request_body { max_size 25MB }
  reverse_proxy 127.0.0.1:14084 {
    transport http { read_timeout 60s write_timeout 60s }
  }
  log { output file /var/log/caddy/o.<домен>.log }
}
```

`/etc/caddy/conf.d/ocpp.<домен>.conf`:

```caddyfile
ocpp.<домен> {
  encode gzip zstd
  header { Strict-Transport-Security "max-age=31536000; includeSubDomains; preload" }
  request_body { max_size 100MB }
  reverse_proxy 127.0.0.1:18900 {
    transport http { read_timeout 600s write_timeout 600s dial_timeout 10s }
  }
  log { output file /var/log/caddy/ocpp.<домен>.log }
}
```

`/etc/caddy/conf.d/supabase.<домен>.conf`:

```caddyfile
supabase.<домен> {
  encode gzip zstd
  header { Strict-Transport-Security "max-age=31536000; includeSubDomains; preload" }
  request_body { max_size 100MB }
  reverse_proxy 127.0.0.1:18800 {
    transport http { read_timeout 300s write_timeout 300s }
  }
  log { output file /var/log/caddy/supabase.<домен>.log }
}
```

Подготовь log-файлы и применяй:

```bash
sudo touch /var/log/caddy/{o,ocpp,supabase}.<домен>.log
sudo chown caddy:caddy /var/log/caddy/*.log
sudo caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile
```

> ⚠️ `systemctl reload caddy` иногда подвисает на ssh; используй `caddy reload --config ...` вместо.

---

## 6. Coolify project «OCharge»

Через UI или API. Через API:

```bash
CO=<coolify-api-token>
COOL=https://c.<домен>

# 6.1. Project
PROJ=$(curl -s -X POST -H "Authorization: Bearer $CO" -H "Content-Type: application/json" \
  $COOL/api/v1/projects \
  -d '{"name":"OCharge","description":"O Charge"}' | jq -r .uuid)
echo "PROJECT=$PROJ"

SERVER=<server-uuid-из-Coolify>
ENV=production
```

---

## 7. Self-hosted Supabase

```bash
# 7.1. Создаём service
SB=$(curl -s -X POST -H "Authorization: Bearer $CO" -H "Content-Type: application/json" \
  $COOL/api/v1/services \
  -d "{
    \"type\":\"supabase\",
    \"name\":\"ocharge-supabase\",
    \"project_uuid\":\"$PROJ\",
    \"environment_name\":\"$ENV\",
    \"server_uuid\":\"$SERVER\",
    \"instant_deploy\":false
  }" | jq -r .uuid)
echo "SUPABASE=$SB"
```

### 7.2. Патчим compose: публикуем порты на хост

Coolify-Caddy не управляет домены сервиса, поэтому публикуем `kong:8000` и `db:5432` на `127.0.0.1`. Получаем текущий `docker_compose_raw`, добавляем `ports` к `supabase-kong` и `supabase-db`, отправляем PATCH (тело base64).

```bash
# Получить
RAW=$(curl -s -H "Authorization: Bearer $CO" $COOL/api/v1/services/$SB | jq -r .docker_compose_raw)

# Вставка ports
echo "$RAW" | python3 -c "
import sys
raw = sys.stdin.read()
raw = raw.replace('supabase-kong:\n    image:',
                  'supabase-kong:\n    ports:\n      - \"127.0.0.1:18800:8000\"\n    image:')
raw = raw.replace('supabase-db:\n    image:',
                  'supabase-db:\n    ports:\n      - \"127.0.0.1:5433:5432\"\n    image:')
sys.stdout.write(raw)
" > /tmp/sb.yml

# PATCH (base64)
python3 -c "import json,base64; print(json.dumps({'docker_compose_raw': base64.b64encode(open('/tmp/sb.yml').read().encode()).decode()}))" \
  | curl -s -X PATCH -H "Authorization: Bearer $CO" -H "Content-Type: application/json" \
    $COOL/api/v1/services/$SB --data @-
```

### 7.3. Публичные URL для Supabase

```bash
for KV in \
  "SUPABASE_PUBLIC_URL=https://supabase.<домен>" \
  "SITE_URL=https://o.<домен>" \
  "API_EXTERNAL_URL=https://supabase.<домен>" \
  "ADDITIONAL_REDIRECT_URLS=https://o.<домен>/auth/callback,https://o.<домен>/**" ; do
  K="${KV%%=*}"; V="${KV#*=}"
  curl -s -X PATCH -H "Authorization: Bearer $CO" -H "Content-Type: application/json" \
    $COOL/api/v1/services/$SB/envs \
    -d "{\"key\":\"$K\",\"value\":\"$V\"}"
done
```

### 7.4. Деплой

```bash
curl -s -X POST -H "Authorization: Bearer $CO" "$COOL/api/v1/deploy?uuid=$SB&force=true"
```

Подожди 3–5 минут пока 14 контейнеров поднимутся (kong/auth/rest/realtime/storage/db/studio/meta/analytics/imgproxy/supavisor/edge-functions/vector/minio).

### 7.5. Извлеки секреты для backend/PWA

```bash
ssh <user>@<сервер> "sudo cat /data/coolify/services/$SB/.env" | \
  grep -E '^(SERVICE_PASSWORD_JWT|SERVICE_SUPABASEANON_KEY|SERVICE_SUPABASESERVICE_KEY|SERVICE_PASSWORD_POSTGRES|SERVICE_USER_ADMIN|SERVICE_PASSWORD_ADMIN)='
```

Запиши значения — пойдут в env backend и `.env` PWA.

---

## 8. Применение схемы БД

В репе `O-Charge-backend/backend/sql/` лежат 25 файлов. Применяй ВСЕ кроме `099_demo_data.sql` и `100_seed_connector_sessions.sql` для прода (в dev/staging — со всеми):

```bash
# Скопировать SQL на сервер
scp O-Charge-backend/backend/sql/*.sql <user>@<сервер>:/tmp/ocharge-sql/

# Прокинуть в supabase-db контейнер
ssh <user>@<сервер> "
DB=\$(sudo docker ps --format '{{.Names}}' | grep supabase-db | head -1)
sudo docker exec \$DB mkdir -p /tmp/sql
for f in /tmp/ocharge-sql/*.sql; do sudo docker cp \"\$f\" \"\$DB:/tmp/sql/\"; done

# Применить 000-022
for f in \$(sudo docker exec \$DB sh -c 'ls /tmp/sql/ | grep -E ^0[0-2][0-9]_ | sort'); do
  echo \"--- \$f ---\"
  sudo docker exec \$DB sh -c \"psql -U postgres -d postgres -v ON_ERROR_STOP=0 -f /tmp/sql/\$f\" 2>&1 | tail -3
done

# (Опционально) демо-данные для staging
sudo docker exec \$DB sh -c 'psql -U postgres -d postgres -f /tmp/sql/099_demo_data.sql'
sudo docker exec \$DB sh -c 'psql -U postgres -d postgres -f /tmp/sql/100_seed_connector_sessions.sql'
"
```

После этого вручную создаём `guest_sessions` (нет в `000_base_schema_local.sql`):

```sql
CREATE TABLE IF NOT EXISTS guest_sessions (
  id              VARCHAR PRIMARY KEY,
  phone           VARCHAR(20) NOT NULL,
  station_id      VARCHAR     NOT NULL,
  connector_id    INTEGER,
  amount_kgs      NUMERIC(10,2) NOT NULL,
  status          VARCHAR(20) NOT NULL DEFAULT 'pending'
                  CHECK (status IN ('pending','paid','charging','completed','cancelled','failed')),
  charging_session_id VARCHAR,
  payment_invoice_id  VARCHAR,
  paid_at         TIMESTAMPTZ,
  started_at      TIMESTAMPTZ,
  completed_at    TIMESTAMPTZ,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_guest_sessions_phone   ON guest_sessions(phone);
CREATE INDEX idx_guest_sessions_status  ON guest_sessions(status);
CREATE INDEX idx_guest_sessions_station ON guest_sessions(station_id);
CREATE INDEX idx_guest_sessions_payment ON guest_sessions(payment_invoice_id);
```

И PWA-миграции (RLS + RPC + owner):

```bash
scp O-Charge-pwa/supabase_migrations/*.sql <user>@<сервер>:/tmp/pwa-sql/
# Применить аналогично — порядок не критичен, ON_ERROR_STOP=0
```

---

## 9. Backend (FastAPI + Redis OCPP)

### 9.1. Создание application

```bash
BE=$(curl -s -X POST -H "Authorization: Bearer $CO" -H "Content-Type: application/json" \
  $COOL/api/v1/applications/public \
  -d "{
    \"project_uuid\":\"$PROJ\",
    \"environment_name\":\"$ENV\",
    \"server_uuid\":\"$SERVER\",
    \"git_repository\":\"https://github.com/Sijjia/O-Charge-backend\",
    \"git_branch\":\"main\",
    \"build_pack\":\"dockercompose\",
    \"docker_compose_location\":\"/docker-compose.production.yml\",
    \"name\":\"ocharge-backend\",
    \"ports_exposes\":\"9210\",
    \"instant_deploy\":false
  }" | jq -r .uuid)

# FQDN для compose-сервиса
curl -s -X PATCH -H "Authorization: Bearer $CO" -H "Content-Type: application/json" \
  $COOL/api/v1/applications/$BE \
  -d '{"docker_compose_domains":[{"name":"evpower-backend","domain":"https://ocpp.<домен>"}]}'
```

### 9.2. Подключение к docker-сети Supabase

В `docker-compose.production.yml` (репа backend) уже:

```yaml
services:
  evpower-backend:
    ports:
      - "127.0.0.1:18900:9210"     # для host-Caddy
    networks:
      - coolify
      - supabase                    # для прямого доступа к supabase-db
  redis:
    networks: [coolify, supabase]

networks:
  coolify: { external: true }
  supabase:
    external: true
    name: <SB-uuid>                 # <- подставить SB UUID из 7.1
```

> Важно: имя сети = UUID Supabase сервиса. Если деплой на другой инстанс — меняй вручную.

### 9.3. Backend env

Через Coolify API (PATCH /applications/$BE/envs). Полный набор:

```text
APP_ENV=production
APP_HOST=0.0.0.0
APP_PORT=9210
LOG_LEVEL=INFO
DOMAIN=https://ocpp.<домен>
ALLOWED_HOSTS=*
CORS_ORIGINS=https://o.<домен>,https://ocpp.<домен>,capacitor://localhost
ENABLE_SWAGGER=false
RATE_LIMIT_DEFAULT_PER_MINUTE=60
RATE_LIMIT_CRITICAL_PER_MINUTE=10
RATE_LIMIT_WEBHOOK_PER_MINUTE=30

# Supabase (из шага 7.5)
DATABASE_URL=postgresql://postgres:<SERVICE_PASSWORD_POSTGRES>@supabase-db:5432/postgres
SUPABASE_URL=https://supabase.<домен>
SUPABASE_ANON_KEY=<SERVICE_SUPABASEANON_KEY>
SUPABASE_SERVICE_ROLE_KEY=<SERVICE_SUPABASESERVICE_KEY>
SUPABASE_JWT_SECRET=<SERVICE_PASSWORD_JWT>
SUPABASE_JWKS_URL=https://supabase.<домен>/auth/v1/.well-known/jwks.json
JWT_VERIFY_ISS=https://supabase.<домен>/auth/v1
JWT_VERIFY_AUD=authenticated

# Сгенерировать локально (python -c "import secrets; print(secrets.token_urlsafe(48))")
SECRET_KEY=<random-48>
CLIENT_FALLBACK_SECRET=<random-32>
STATION_MASTER_API_KEY=<random-32>
EZS_SECRET_KEY=<random-32>

# OCPP — авторизация станций отключена (KG не поддерживает)
VERIFY_STATION_API_KEYS=false
OCPP_PROTOCOL_VERSION=1.6
OCPP_WS_PORT=9210

# Dev login (отключить в проде)
ALLOW_DEV_LOGIN=true

# Платежи — placeholder (заменить когда ODENGI/OBANK подключат)
PAYMENT_PROVIDER=ODENGI
ODENGI_MERCHANT_ID=placeholder
ODENGI_PASSWORD=placeholder
ODENGI_WEBHOOK_SECRET=placeholder-webhook-secret-32chars-long
ODENGI_USE_PRODUCTION=false
DEFAULT_CURRENCY=KGS
QR_CODE_LIFETIME_MINUTES=5
INVOICE_LIFETIME_MINUTES=10
STATUS_CHECK_INTERVAL_SECONDS=15
PAYMENT_TIMEOUT_MINUTES=30

# Push (VAPID — сгенерировать через openssl)
PUSH_NOTIFICATIONS_ENABLED=true
PUSH_TTL=86400
VAPID_SUBJECT=mailto:noreply@<домен>
VAPID_PRIVATE_KEY=<P-256 private base64url, 32 байта>
VAPID_PUBLIC_KEY=<P-256 public base64url, 65 байт>
```

VAPID-генерация:

```bash
TMP=$(mktemp -d)
for i in 1 2 3 4 5; do
  openssl ecparam -name prime256v1 -genkey -noout -out "$TMP/k.pem" 2>/dev/null
  PRIV=$(openssl ec -in "$TMP/k.pem" -text -noout 2>/dev/null | awk '/priv:/{f=1;next}/pub:/{f=0}f' | tr -d ' :\n')
  PUB=$(openssl ec -in "$TMP/k.pem" -text -noout 2>/dev/null | awk '/pub:/{f=1;next}/ASN1/{f=0}f' | tr -d ' :\n')
  [ "${#PRIV}" = "64" ] && [ "${#PUB}" = "130" ] && break
done
python3 -c "import base64,binascii,sys; print(base64.urlsafe_b64encode(binascii.unhexlify(sys.argv[1])).rstrip(b'=').decode())" $PRIV
python3 -c "import base64,binascii,sys; print(base64.urlsafe_b64encode(binascii.unhexlify(sys.argv[1])).rstrip(b'=').decode())" $PUB
```

### 9.4. Деплой

```bash
curl -s -X POST -H "Authorization: Bearer $CO" "$COOL/api/v1/deploy?uuid=$BE&force=true"
```

Проверка:

```bash
curl -s https://ocpp.<домен>/health
# → {"status":"healthy","redis":"connected",...}
```

---

## 10. PWA (Vite/React + Capacitor)

### 10.1. .env в репе

`O-Charge-pwa/.env` коммитится в репу — там только публичные значения (anon key, public URLs):

```env
VITE_API_URL=https://ocpp.<домен>
VITE_WEBSOCKET_URL=wss://ocpp.<домен>
VITE_SUPABASE_URL=https://supabase.<домен>
VITE_SUPABASE_ANON_KEY=<SERVICE_SUPABASEANON_KEY>
VITE_AUTH_MODE=cookie
VITE_VAPID_PUBLIC_KEY=<тот же VAPID_PUBLIC из 9.3>
VITE_2GIS_API_KEY=<2GIS-key>
VITE_DEMO_MODE=false
VITE_ENABLE_AUTH_REFRESH=true
VITE_ALLOW_DEV_LOGIN=true   # для staging
```

> Coolify build-time env через Nixpacks **не пропадает** в Vite — поэтому значения держим в `.env` в репе. Anon key публичный, не секрет.

### 10.2. Coolify application

```bash
PWA=$(curl -s -X POST -H "Authorization: Bearer $CO" -H "Content-Type: application/json" \
  $COOL/api/v1/applications/public \
  -d "{
    \"project_uuid\":\"$PROJ\",
    \"environment_name\":\"$ENV\",
    \"server_uuid\":\"$SERVER\",
    \"git_repository\":\"https://github.com/Sijjia/O-Charge-pwa\",
    \"git_branch\":\"main\",
    \"build_pack\":\"nixpacks\",
    \"name\":\"ocharge-pwa\",
    \"ports_exposes\":\"3000\",
    \"domains\":\"https://o.<домен>\",
    \"instant_deploy\":false
  }" | jq -r .uuid)

# Port mapping для host-Caddy
curl -s -X PATCH -H "Authorization: Bearer $CO" -H "Content-Type: application/json" \
  $COOL/api/v1/applications/$PWA \
  -d '{"ports_mappings":"14084:3000"}'

# NIXPACKS_NODE_VERSION=22 (если не подхватывается из package.json)
curl -s -X POST -H "Authorization: Bearer $CO" -H "Content-Type: application/json" \
  $COOL/api/v1/applications/$PWA/envs \
  -d '{"key":"NIXPACKS_NODE_VERSION","value":"22","is_buildtime":true}'

# Деплой
curl -s -X POST -H "Authorization: Bearer $CO" "$COOL/api/v1/deploy?uuid=$PWA&force=true"
```

Проверка:

```bash
curl -sI https://o.<домен>/
# → HTTP/2 200, content-type text/html
```

---

## 11. Smoke-тест прод-готовности

```bash
DOMAIN=<твой-домен>

# DNS
dig +short o.$DOMAIN ocpp.$DOMAIN supabase.$DOMAIN

# TLS LE
for d in o ocpp supabase; do
  echo | openssl s_client -connect $d.$DOMAIN:443 -servername $d.$DOMAIN 2>/dev/null \
    | openssl x509 -noout -issuer -dates
done

# PWA
curl -sI https://o.$DOMAIN/

# Backend
curl -s https://ocpp.$DOMAIN/health

# Supabase
ANON=<SUPABASE_ANON_KEY>
curl -s -H "apikey: $ANON" https://supabase.$DOMAIN/auth/v1/health
curl -s -H "apikey: $ANON" "https://supabase.$DOMAIN/rest/v1/locations?limit=1"

# OCPP WSS (внутри backend контейнера или через wscat)
# wscat -c wss://ocpp.$DOMAIN/ws/<реальный-station-id> -s ocpp1.6
```

---

## 12. Production checklist

- [ ] DNS TTL поднят с 60 до 3600 после стабилизации
- [ ] `VERIFY_STATION_API_KEYS` — оставить `false` (KG-станции не поддерживают)
- [ ] `ALLOW_DEV_LOGIN=false` для прода
- [ ] `ENABLE_SWAGGER=false`
- [ ] ODENGI / OBANK реальные credentials
- [ ] Auto-renew домена (`.ai` стоит $90/год — поставить календарь T-60)
- [ ] Backup Postgres: `pg_dump` через cron, отгрузка во внешнее хранилище
- [ ] Mobile (Capacitor) bundle пересобрать с новым `VITE_API_URL`/`VITE_SUPABASE_URL` и опубликовать
- [ ] Force-update механизм в mobile если домен меняется
- [ ] Мониторинг: uptime checks на `o`/`ocpp`/`supabase`, алерты на `redis disconnected`, на свободное место Postgres volume

---

## 13. Recovery — что делать если сервер сдох

1. Поднять новый VPS (Hetzner CPX31 / Contabo VPS-M / Vultr High Frequency)
2. Снапшот Postgres из backup (см. checklist пункт 6)
3. Указать новый IP в Cloudflare A-записях
4. Caddy сам выпустит LE через 30–60 секунд после DNS propagation
5. Coolify pulls latest from GitHub, env уже сохранены — деплой автоматический
6. Восстановить Postgres dump в новый supabase-db
7. Smoke по шагу 11

Время восстановления: 30–60 минут при готовом backup.

---

## 14. Где что лежит

| Что | Где |
|---|---|
| PWA код | `github.com/Sijjia/O-Charge-pwa` |
| Backend код | `github.com/Sijjia/O-Charge-backend` |
| SQL миграции | `O-Charge-backend/backend/sql/000-022_*.sql` |
| RLS / RPC миграции | `O-Charge-pwa/supabase_migrations/*.sql` |
| Coolify config | `https://c.<домен>` (login через email) |
| Caddy config | `/etc/caddy/Caddyfile` + `/etc/caddy/conf.d/*.conf` |
| Supabase env | `/data/coolify/services/<SB-UUID>/.env` |
| Логи прокси | `/var/log/caddy/*.log` |
| Логи backend | `coolify panel → ocharge-backend → Logs` или `docker logs <container>` |

---

_Last verified: 2026-05-05 на orion (65.21.205.230) с Coolify c.asystem.ai. Зона `asystem.ai` (Cloudflare). Подопытный сценарий — переезд после блокировки proximus._
