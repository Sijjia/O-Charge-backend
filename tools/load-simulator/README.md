# OCPP Load Simulator

Симулятор нагрузки для тестирования OCPP WebSocket сервера с тысячами одновременных станций.

## Установка

```bash
cd ocpp-rp/tools/load-simulator
npm install
```

## Запуск

```bash
# 100 станций (быстрый тест)
npm run start:100

# 1000 станций
npm run start:1000

# 10,000 станций (полная нагрузка)
npm run start:10000

# Кастомная конфигурация
STATIONS=5000 WS_URL=ws://localhost:9210/ws CHARGING_PROBABILITY=0.2 npm start
```

## Переменные окружения

| Переменная | По умолчанию | Описание |
|-----------|-------------|----------|
| `WS_URL` | `ws://localhost:9210/ws` | WebSocket URL сервера |
| `STATIONS` | `100` | Количество виртуальных станций |
| `BATCH_SIZE` | `50` | Станций в одном батче подключения |
| `BATCH_DELAY` | `200` | Задержка между батчами (мс) |
| `HEARTBEAT_INTERVAL` | `30000` | Интервал Heartbeat (мс) |
| `METER_INTERVAL` | `10000` | Интервал MeterValues (мс) |
| `CHARGING_PROBABILITY` | `0.1` | Вероятность начала зарядки (0-1) |
| `CONNECTORS` | `2` | Кол-во коннекторов на станцию |
| `STATION_PREFIX` | `LOAD-SIM` | Префикс ID станций |
| `VERBOSE` | `false` | Подробное логирование ошибок |

## Что симулирует каждая станция

1. **BootNotification** — при подключении
2. **StatusNotification** — для каждого коннектора (Available)
3. **Heartbeat** — каждые 30 сек (+ случайный jitter)
4. **Authorize** → **StartTransaction** — случайное начало зарядки
5. **MeterValues** — каждые 10 сек для активных зарядок (Energy + Power)
6. **StopTransaction** — завершение зарядки
7. **RemoteStart/Stop** — ответы на серверные команды

## Рекомендации для 10K станций

- Увеличить ulimit: `ulimit -n 65536`
- Сервер: минимум 8 vCPU / 16GB RAM
- Redis: `maxclients 20000`
- Nginx: `worker_connections 65536`
- Node.js: `--max-old-space-size=4096`

## Мониторинг

Во время работы выводятся статы каждые 10 сек:
```
[120s] Connected: 10,000 | Charging: 980 | Sent: 245,000 | Recv: 244,500 | Rate: 2041 msg/s | Errors: 0
```

Ctrl+C для graceful shutdown с итоговой статистикой.
