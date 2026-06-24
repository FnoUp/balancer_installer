# VPN Balancer — Руководство пользователя

> Автоматический балансировщик нагрузки для VPN-инфраструктуры на базе Remnawave + Prometheus.
> Следит за здоровьем нод, выводит плохие из пула и возвращает хорошие, уведомляет в Telegram.

---

## Как это работает (за 30 секунд)

```
Prometheus  ←──  node_exporter (на каждой ноде)
                 ping_metrics.sh  (пинг → 77.88.8.8)
                 speedtest.sh     (скорость канала)

balancer.py ──→  читает метрики из Prometheus
            ──→  считает score (0.0 = отлично, 1.0 = мертво)
            ──→  управляет тегом хоста в Remnawave (/api/hosts)
            ──→  шлёт уведомления в Telegram
```

Каждые **2 минуты** балансировщик проверяет каждую ноду:
- score > **0.75** → нода выводится из пула (тег снимается)
- score < **0.55** → нода возвращается в пул (тег ставится)

---

## Файлы проекта

```
setup.sh          ← установка всего на сервер панели
add_node.py       ← добавить новую ноду в мониторинг
balancer_template.py  ← шаблон балансировщика (%%ЗАГЛУШКИ%%)
sim_alerts.py     ← симуляция всех 19 уведомлений (тест)
real_alerts.py    ← реальные уведомления с живыми метриками
```

**На сервере панели (163.5.16.247):**
```
/opt/vpn-balancer/balancer.py       ← рабочий балансировщик
/etc/vpn-balancer/config            ← конфигурация (SVC_NAME и т.п.)
/etc/vpn-balancer/node_first_seen.json  ← время добавления каждой ноды
/var/log/vpn-balancer/balancer.log  ← лог работы
```

**На каждой ноде (138.x.x.x, 45.x.x.x, ...):**
```
/etc/vpn-balancer/ping_metrics.sh   ← пинг к 77.88.8.8 (каждые 5 мин)
/etc/vpn-balancer/speedtest.sh      ← speedtest канала (каждые 8 ч)
/var/lib/node_exporter/textfile_collector/vpn_metrics.prom  ← результаты
```

---

## Установка (один раз)

### Шаг 1 — Сервер панели

```bash
# Клонируй репозиторий
git clone https://github.com/FnoUp/balancer_installer.git
cd balancer_installer

# Скопируй на сервер
scp setup.sh root@163.5.16.247:/tmp/

# Запусти установку
ssh root@163.5.16.247
bash /tmp/setup.sh
```

Скрипт спросит:
- Токен Remnawave (`Admin → API Keys`)
- Токен Telegram бота
- ID чатов и топиков для уведомлений
- Домен панели

### Шаг 2 — Каждая нода

```bash
# setup.sh делает это автоматически при выборе "Установка на ноду"
# Или вручную:
ssh root@<IP_НОДЫ>
# Далее setup.sh определит тип (нода) и установит node_exporter + скрипты
```

---

## Добавить новую ноду

```bash
ssh root@163.5.16.247
python3 /opt/vpn-balancer/add_node.py
```

Скрипт спросит:
| Поле | Где взять |
|------|-----------|
| Название ноды | любое (fr1, de1, fi1) |
| IP адрес ноды | IP вашего VPS |
| Локация | france / finland / germany |
| Сетевой интерфейс | обычно `eth0` (проверь `ip link`) |
| UUID хоста Remnawave | Панель → Hosts → нужный хост → UUID |

Скрипт автоматически:
- Добавит цели в Prometheus
- Добавит ноду в список балансировщика
- Перезапустит сервисы
- Отправит уведомление в Telegram

---

## Настройки балансировщика

Все переменные в `/opt/vpn-balancer/balancer.py` (секция констант):

### Пороги score
```python
SCORE_BAD  = 0.75   # выше → нода выводится из пула
SCORE_GOOD = 0.55   # ниже → нода возвращается в пул
```
*Гистерезис 0.75/0.55 предотвращает «мерцание» — нода не прыгает туда-обратно каждые 2 минуты.*

### Критические пороги (→ score = 1.0 принудительно)
```python
CPU_CRITICAL = 90.0   # % — CPU выше этого = форс-мажор
RAM_CRITICAL = 90.0   # % — RAM выше этого = форс-мажор
MAX_PING_MS  = 300.0  # мс — пинг выше этого = форс-мажор
MAX_USERS    = 100    # юзеров — переполнение (алерт, не форс-мажор)
```

### Расчёт capacity (потолок канала)
```python
MIN_SPEEDTEST     = 100.0   # Мбит/с — ниже → штраф capacity=50
CAPACITY_FLOOR    = 50.0    # штраф при медленном/без данных speedtest
CAPACITY_GRACE    = 100.0   # для новых нод (<24ч, нет speedtest)
NODE_GRACE_PERIOD = 86400   # 24ч = 86400 секунд
```

| Условие | capacity |
|---------|----------|
| speedtest ≥ 100 Мбит/с | = speedtest (реальный потолок) |
| speedtest < 100 Мбит/с | = 50 (штраф) |
| нет speedtest + нода < 24ч | = 100 (grace, не штрафуем новую) |
| нет speedtest + нода ≥ 24ч + есть tx_p95 | = tx_p95 (реальный трафик) |
| нет данных | = 50 (штраф) |

### Веса компонентов score
```python
W_PING = 0.25   # пинг к 77.88.8.8
W_BW   = 0.50   # текущий tx / capacity  ← самый важный
W_USER = 0.15   # активные юзеры / 100
W_CPU  = 0.07   # загрузка CPU
W_RAM  = 0.03   # загрузка RAM
```

*Сумма весов = 1.0. score ∈ [0.0, 1.0], чем выше — тем хуже.*

### Интервалы
```python
CHECK_INTERVAL = 120    # секунды между проверками
DIGEST_HOUR    = 9      # час UTC для отправки дайджеста
ALERT_COOLDOWN = 1800   # 30 мин между повторными алертами одного типа
```

---

## Уведомления

Три чата в Telegram:

### 📊 Метрики (топик 233)
| # | Уведомление | Когда |
|---|-------------|-------|
| 01 | 🚀 Балансировщик запущен | при старте сервиса |
| 02 | ❌ Prometheus недоступен | не читаются метрики |
| 03 | ❌ Ошибка API Remnawave | не удалось обновить тег |
| 04 | 📡 Пинг недоступен | 77.88.8.8 не отвечает |
| 05 | 📡 Пинг высокий | > 300 мс |
| 06 | ✅ Пинг восстановился | после 04 или 05 |
| 07 | ⚠️ CPU критично | ≥ 90% |
| 08 | ✅ CPU восстановился | после 07 |
| 09 | ⚠️ RAM критично | ≥ 90% |
| 10 | ✅ RAM восстановился | после 09 |
| 11 | 🐢 Нода медленная | speedtest < 100 Мбит/с |
| 12 | 👥 Нода переполнена | юзеров ≥ 100 |
| 13 | 🔴 Нода выведена из пула | score > 0.75 |
| 14 | 🟢 Нода возвращена в пул | score < 0.55 |
| 15 | 🟢 Новая нода подключена | после add_node.py |

### 🚨 Критика (топик 4)
| # | Уведомление | Когда |
|---|-------------|-------|
| 16 | ⚠️ Деградация — последняя нода перегружена | score > 0.75, нода одна — **выводить нельзя**, пользователи ещё в сети |
| 17 | 🚨 Сервис недоступен — все ноды упали | пул пуст, подключения невозможны |

> **Разница:** 16 — сервис деградировал (нода в пуле), 17 — сервис упал (0 нод).

### 📋 Отчёты (топик 6)
| # | Уведомление | Когда |
|---|-------------|-------|
| 18 | 📊 Дайджест нод | ежедневно в 09:00 UTC |
| 19 | 📋 Лог за сутки | сразу после дайджеста |

---

## Формула score

```
score = ping_score × 0.25
      + bw_score   × 0.50
      + user_score × 0.15
      + cpu_score  × 0.07
      + ram_score  × 0.03

где:
  ping_score = clamp(ping_ms / 300)
  bw_score   = clamp(tx_mbps / capacity)
  user_score = clamp(users / 100)
  cpu_score  = clamp(cpu_pct / 100)
  ram_score  = clamp(ram_pct / 100)
  clamp(x)   = min(1.0, max(0.0, x))
```

Если пинг недоступен / CPU ≥ 90% / RAM ≥ 90% — score = **1.0** (принудительно).

**Примеры:**
- Отличная нода: ping=50ms, tx=5/700Мбит/с, 3 юзера, cpu=3%, ram=34% → score ≈ **0.06**
- Нагруженная: ping=150ms, tx=45/50Мбит/с, 60 юзеров, cpu=70%, ram=75% → score ≈ **0.67**
- Мёртвая: ping недоступен → score = **1.0**

---

## Диагностика

### Проверить, что балансировщик работает
```bash
ssh root@163.5.16.247
systemctl status vpn-balancer
tail -f /var/log/vpn-balancer/balancer.log
```

### Посмотреть текущий score нод
```bash
# В логе ищи строки вида:
# 2026-06-24 09:02:01 [INFO] France: score=0.056 users=3 | ping=52ms bw=1.3/712Мбит/с ...
grep "score=" /var/log/vpn-balancer/balancer.log | tail -20
```

### Проверить метрики Prometheus
```bash
# Открыть в браузере:
http://163.5.16.247:9090/targets     # все цели (должны быть UP)
http://163.5.16.247:9090/graph       # ручные запросы

# Пример: текущий пинг France
vpn_node_ping_ms{instance="138.124.251.48:9100"}
```

### Проверить, что node_exporter читает метрики с ноды
```bash
# С сервера панели:
ssh root@163.5.16.247
curl http://138.124.251.48:9100/metrics | grep vpn_node
# Должны быть vpn_node_ping_ms, vpn_node_ping_ok, vpn_node_capacity_mbps
```

### Принудительно запустить speedtest / пинг на ноде
```bash
ssh root@138.124.251.48
bash /etc/vpn-balancer/ping_metrics.sh
bash /etc/vpn-balancer/speedtest.sh
cat /var/lib/node_exporter/textfile_collector/vpn_metrics.prom
```

### Отправить тестовые уведомления
```bash
# Все 19 сценариев (симуляция):
python3 /tmp/sim_alerts.py

# Реальные данные:
scp real_alerts.py root@163.5.16.247:/tmp/
python3 /tmp/real_alerts.py
```

---

## Частые проблемы

| Симптом | Причина | Решение |
|---------|---------|---------|
| `node_textfile_scrape_error 1` в метриках | vpn_metrics.prom недоступен node_exporter | `chmod 644 /var/lib/node_exporter/textfile_collector/vpn_metrics.prom` |
| Уведомления дублируются | двойное логирование | в systemd-сервисе должно быть `StandardOutput=journal` |
| `users=?` в логе | нет ответа от `/api/nodes` | проверь REMNAWAVE_TOKEN и REMNAWAVE_COOKIE в balancer.py |
| Speedtest 0 или нет | cron не запустился | `crontab -l` на ноде, проверь `/etc/vpn-balancer/speedtest.sh` |
| Score всегда 0 | нет tx-трафика (нода пустая) | нормально, при росте нагрузки score вырастет |
| Нода не возвращается в пул | score выше 0.55 | посмотри метрики в Prometheus, найди узкое место |

---

## Структура данных нод в balancer.py

```python
NODES = [
    {
        "name":          "🇫🇷 France",      # имя в TG-уведомлениях
        "host_uuid":     "xxxxxxxx-xxxx-...", # UUID хоста в Remnawave
        "prom_instance": "138.124.251.48:9100", # цель Prometheus
        "ping_instance": "138.124.251.48",   # IP для blackbox probe
        "net_device":    "eth0",             # сетевой интерфейс на ноде
    },
    # ... следующие ноды
]
```

UUID берётся из Remnawave: `Admin → Hosts → <нода> → UUID`.  
После изменения этого файла: `systemctl restart vpn-balancer`.

---

## Версии файлов

| Файл | Назначение |
|------|------------|
| `setup.sh` | Полная установка (панель + нода) |
| `balancer_template.py` | Шаблон (с `%%ЗАГЛУШКАМИ%%`) — не запускать напрямую |
| `add_node.py` | Добавление ноды, запускается на панели |
| `sim_alerts.py` | Тест: все 19 TG-уведомлений с тестовыми данными |
| `real_alerts.py` | Тест: реальные уведомления с данными из Prometheus |
