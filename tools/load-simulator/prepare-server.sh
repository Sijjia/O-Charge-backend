#!/bin/bash
# ══════════════════════════════════════════════════════════════════════
# Подготовка VPS к стресс-тесту — запускать на сервере (62.171.149.139)
# ══════════════════════════════════════════════════════════════════════

set -e

echo "🔧 Подготовка сервера к стресс-тесту..."

# 1. Увеличиваем лимиты файловых дескрипторов
echo ""
echo "1️⃣  Настройка ulimits..."
cat >> /etc/security/limits.conf << 'EOF'
# Added for OCPP stress test
*    soft    nofile    65536
*    hard    nofile    65536
root soft    nofile    65536
root hard    nofile    65536
EOF

# Для текущей сессии
ulimit -n 65536 2>/dev/null || echo "  ⚠️  ulimit -n не удалось (перезайти в SSH)"

# 2. Настройка ядра для большого количества TCP соединений
echo "2️⃣  Настройка sysctl..."
cat >> /etc/sysctl.conf << 'EOF'

# === OCPP Stress Test Tuning ===
net.core.somaxconn = 65535
net.core.netdev_max_backlog = 65535
net.ipv4.tcp_max_syn_backlog = 65535
net.ipv4.ip_local_port_range = 1024 65535
net.ipv4.tcp_tw_reuse = 1
net.ipv4.tcp_fin_timeout = 15
net.ipv4.tcp_keepalive_time = 300
net.ipv4.tcp_keepalive_intvl = 30
net.ipv4.tcp_keepalive_probes = 5
fs.file-max = 200000
fs.nr_open = 200000
net.core.rmem_max = 16777216
net.core.wmem_max = 16777216
EOF

sysctl -p 2>/dev/null || echo "  ⚠️  sysctl apply partial"

# 3. Настройка nginx для большого количества WebSocket
echo "3️⃣  Настройка nginx worker_connections..."
sed -i 's/worker_connections.*/worker_connections 65535;/' /etc/nginx/nginx.conf
# Добавляем worker_rlimit_nofile если нет
grep -q "worker_rlimit_nofile" /etc/nginx/nginx.conf || \
    sed -i '/^worker_processes/a worker_rlimit_nofile 65536;' /etc/nginx/nginx.conf

nginx -t && systemctl reload nginx
echo "  ✅ nginx обновлён"

# 4. Увеличиваем Redis maxclients
echo "4️⃣  Настройка Redis..."
docker exec evpower-redis-dev redis-cli CONFIG SET maxclients 50000 2>/dev/null || echo "  ⚠️  Redis config failed"

# 5. Проверка
echo ""
echo "══════════════════════════════════════════"
echo "  📊 Текущие параметры:"
echo "  ulimit -n:     $(ulimit -n)"
echo "  somaxconn:     $(cat /proc/sys/net/core/somaxconn)"
echo "  file-max:      $(cat /proc/sys/fs/file-max)"
echo "  port range:    $(cat /proc/sys/net/ipv4/ip_local_port_range)"
echo "  nginx workers: $(grep worker_connections /etc/nginx/nginx.conf)"
echo "══════════════════════════════════════════"

echo ""
echo "✅ Сервер подготовлен! Теперь перезапустите uvicorn:"
echo ""
echo "  cd /root/ocpp-rp/backend"
echo "  kill \$(pgrep -f 'uvicorn app.main')"
echo "  ulimit -n 65536"
echo "  nohup python -m uvicorn app.main:app --host 0.0.0.0 --port 9210 --workers 4 &"
echo ""
echo "⚠️  ВАЖНО: После теста верните --workers 1 (экономия RAM)"
