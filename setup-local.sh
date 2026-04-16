#!/bin/bash
# RedPetroleum Backend — Local Development Setup (macOS)
# Запуск: chmod +x setup-local.sh && ./setup-local.sh

set -e

echo "🔧 RedPetroleum Backend — Local Setup"
echo "================================="

# 1. Проверка Homebrew
if ! command -v brew &> /dev/null; then
    echo "❌ Homebrew не установлен. Установи: https://brew.sh"
    exit 1
fi
echo "✅ Homebrew найден"

# 2. Python 3.11
if ! command -v python3.11 &> /dev/null; then
    echo "📦 Устанавливаю Python 3.11..."
    brew install python@3.11
else
    echo "✅ Python 3.11 найден: $(python3.11 --version)"
fi

# 3. Redis
if ! command -v redis-server &> /dev/null; then
    echo "📦 Устанавливаю Redis..."
    brew install redis
fi

# Запуск Redis
if ! redis-cli ping &> /dev/null 2>&1; then
    echo "🚀 Запускаю Redis..."
    brew services start redis
    sleep 2
fi
echo "✅ Redis: $(redis-cli ping)"

# 4. Virtual Environment
cd "$(dirname "$0")/backend"
echo "📍 Working directory: $(pwd)"

if [ ! -d "venv" ]; then
    echo "📦 Создаю venv..."
    python3.11 -m venv venv
fi

source venv/bin/activate
echo "✅ Python: $(python --version)"

# 5. Dependencies
echo "📦 Устанавливаю зависимости..."
pip install --upgrade pip -q
pip install -r requirements.txt -q

# 6. .env
if [ ! -f ".env" ]; then
    echo ""
    echo "⚠️  Файл .env не найден!"
    echo "   Скопируй и заполни:"
    echo "   cp .env.example .env"
    echo "   nano .env  # заполни SUPABASE_*, DATABASE_URL"
    echo ""
    echo "   Минимум нужно:"
    echo "   - DATABASE_URL=postgresql://..."
    echo "   - SUPABASE_URL=https://xxx.supabase.co"
    echo "   - SUPABASE_ANON_KEY=eyJ..."
    echo "   - SUPABASE_SERVICE_ROLE_KEY=eyJ..."
    echo "   - REDIS_URL=redis://localhost:6379/0"
    echo ""
    cp .env.example .env
    echo "📋 .env.example скопирован в .env — заполни Supabase данные!"
    exit 0
fi

# 7. Запуск
echo ""
echo "🚀 Запускаю RedPetroleum Backend на порту 9210..."
echo "   API:       http://localhost:9210"
echo "   Health:    http://localhost:9210/health"
echo "   Swagger:   http://localhost:9210/docs (если ENABLE_SWAGGER=true)"
echo "   WebSocket: ws://localhost:9210/ws/{station_id}"
echo ""
echo "   Ctrl+C для остановки"
echo ""

exec uvicorn app.main:app --host 0.0.0.0 --port 9210 --reload
