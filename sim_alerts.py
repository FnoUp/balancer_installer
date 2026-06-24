#!/usr/bin/env python3
"""Симуляция ВСЕХ 19 вариантов TG-уведомлений балансировщика.

Запуск: python3 sim_alerts.py
"""
import re, requests, time

with open('/opt/vpn-balancer/balancer.py') as f:
    src = f.read()

def cfg(key):
    m = re.search(r'^' + key + r'\s*=\s*["\']([^"\']+)["\']', src, re.MULTILINE)
    return m.group(1) if m else ''

def cfg_int(key):
    m = re.search(r'^' + key + r'\s*=\s*(\d+)', src, re.MULTILINE)
    return int(m.group(1)) if m else 0

BOT    = cfg('TG_BOT_TOKEN')
M_CHAT = cfg('TG_METRICS_CHAT_ID');  M_TOP = cfg_int('TG_METRICS_TOPIC_ID')
E_CHAT = cfg('TG_ERRORS_CHAT_ID');   E_TOP = cfg_int('TG_ERRORS_TOPIC_ID')
R_CHAT = cfg('TG_REPORTS_CHAT_ID');  R_TOP = cfg_int('TG_REPORTS_TOPIC_ID')

print(f'Bot: {BOT[:12]}...')
print(f'Метрики: chat={M_CHAT} topic={M_TOP}')
print(f'Критика: chat={E_CHAT} topic={E_TOP}')
print(f'Отчёты:  chat={R_CHAT} topic={R_TOP}')

results = {'ok': 0, 'fail': 0}

def send(label, chat, topic, text):
    payload = {'chat_id': chat, 'text': text, 'parse_mode': 'HTML'}
    if topic:
        payload['message_thread_id'] = topic
    try:
        r = requests.post(
            f'https://api.telegram.org/bot{BOT}/sendMessage',
            json=payload, timeout=10
        )
        if r.status_code == 200:
            results['ok'] += 1
            print(f'  ✓ {label}')
        else:
            results['fail'] += 1
            print(f'  ✗ {label}: HTTP {r.status_code} {r.json().get("description", "")}')
    except Exception as e:
        results['fail'] += 1
        print(f'  ✗ {label}: {e}')
    time.sleep(0.5)

# Данные для сценариев
NODE_GOOD   = '🇫🇷 France'
NODE_BAD    = '🇫🇮 Finland'
DETAIL_GOOD = 'ping=52ms  bw=1.3/712Мбит/с  users=3  cpu=2.9%  ram=33.5%'
DETAIL_BAD  = 'ping=9999ms  bw=0.1/50Мбит/с  users=47  cpu=91.2%  ram=92.7%'

# ══════════════════════════════════════════════════════
print('\n═══ ЧАТ: МЕТРИКИ ═══')
# ══════════════════════════════════════════════════════

send('01 Балансировщик запущен',
     M_CHAT, M_TOP,
     '🚀 <b>VPN Balancer запущен</b>\nМониторинг нод активен.')

send('02 Prometheus недоступен',
     M_CHAT, M_TOP,
     '❌ <b>Prometheus недоступен</b>\n'
     'Метрики не читаются\n'
     '<code>ConnectionRefusedError: [Errno 111] Connection refused</code>')

send('03 Ошибка API Remnawave',
     M_CHAT, M_TOP,
     '❌ <b>Ошибка API Remnawave</b>\n'
     'Не удалось обновить тег хоста\n'
     '<code>HTTP 503: Service Unavailable</code>')

send('04 Пинг недоступен',
     M_CHAT, M_TOP,
     f'📡 <b>Пинг недоступен</b>\n'
     f'Нода: <b>{NODE_BAD}</b>\n'
     f'77.88.8.8 не отвечает — нода изолирована или нет интернета')

send('05 Пинг критически высокий',
     M_CHAT, M_TOP,
     f'📡 <b>Пинг критически высокий</b>\n'
     f'Нода: <b>{NODE_BAD}</b>\n'
     f'Пинг: <code>347ms</code> (порог 300ms)\n'
     f'<code>{DETAIL_BAD}</code>')

send('06 Пинг восстановился',
     M_CHAT, M_TOP,
     f'✅ <b>Пинг восстановился</b>\n'
     f'Нода: <b>{NODE_BAD}</b>\n'
     f'Пинг: <code>118ms</code>')

send('07 CPU критично',
     M_CHAT, M_TOP,
     f'⚠️ <b>CPU критично</b>\n'
     f'Нода: <b>{NODE_BAD}</b>\n'
     f'CPU: <code>91%</code> (порог 90%) → score принудительно 1.0\n'
     f'<code>{DETAIL_BAD}</code>')

send('08 CPU восстановился',
     M_CHAT, M_TOP,
     f'✅ <b>CPU восстановился</b>\n'
     f'Нода: <b>{NODE_BAD}</b>\n'
     f'CPU: <code>13%</code>')

send('09 RAM критично',
     M_CHAT, M_TOP,
     f'⚠️ <b>RAM критично</b>\n'
     f'Нода: <b>{NODE_BAD}</b>\n'
     f'RAM: <code>93%</code> (порог 90%) → score принудительно 1.0\n'
     f'<code>{DETAIL_BAD}</code>')

send('10 RAM восстановился',
     M_CHAT, M_TOP,
     f'✅ <b>RAM восстановился</b>\n'
     f'Нода: <b>{NODE_BAD}</b>\n'
     f'RAM: <code>59%</code>')

send('11 Нода аномально медленная',
     M_CHAT, M_TOP,
     f'🐢 <b>Нода аномально медленная</b>\n'
     f'Нода: <b>{NODE_BAD}</b>\n'
     f'Speedtest upload: <code>22.4 Мбит/с</code> (порог 100 Мбит/с)\n'
     f'Проверь тариф VPS или сетевой интерфейс')

send('12 Нода переполнена',
     M_CHAT, M_TOP,
     f'👥 <b>Нода переполнена</b>\n'
     f'Нода: <b>{NODE_BAD}</b>\n'
     f'Активных пользователей: <code>100</code> (макс 100)')

send('13 Нода выведена из пула',
     M_CHAT, M_TOP,
     f'🔴 <b>Нода выведена из пула</b>\n'
     f'Нода: <b>{NODE_BAD}</b>\n'
     f'Score: <code>0.83</code> (порог 0.75)\n'
     f'<code>{DETAIL_BAD}</code>')

send('14 Нода возвращена в пул',
     M_CHAT, M_TOP,
     f'🟢 <b>Нода возвращена в пул</b>\n'
     f'Нода: <b>{NODE_BAD}</b>\n'
     f'Score: <code>0.47</code> (порог 0.55)\n'
     f'<code>{DETAIL_GOOD}</code>')

send('15 Новая нода подключена',
     M_CHAT, M_TOP,
     '🟢 <b>Новая нода подключена</b>\n'
     'Нода: <b>🇩🇪 Germany</b>\n'
     'IP: <code>5.10.20.30</code>\n'
     'Локация: germany\n'
     'Интерфейс: eth0\n'
     'Пинг (с панели): <code>28ms</code>\n'
     'Prometheus добавлен, балансировщик перезапущен')

# ══════════════════════════════════════════════════════
print('\n═══ ЧАТ: КРИТИКА ═══')
# ══════════════════════════════════════════════════════

send('16 Деградация — последняя нода перегружена',
     E_CHAT, E_TOP,
     f'⚠️ <b>Деградация — последняя нода перегружена</b>\n'
     f'Нода: <b>{NODE_GOOD}</b>  score=<code>0.79</code>\n'
     f'Пользователи ещё подключены, но качество снижено.\n'
     f'<code>{DETAIL_BAD}</code>')

send('17 Сервис недоступен — все ноды упали',
     E_CHAT, E_TOP,
     f'🚨 <b>СЕРВИС НЕДОСТУПЕН — все ноды упали</b>\n'
     f'Пул пуст, подключения невозможны.\n'
     f'Последней выведена: <b>{NODE_BAD}</b>  score=<code>0.91</code>\n'
     f'<code>{DETAIL_BAD}</code>')

# ══════════════════════════════════════════════════════
print('\n═══ ЧАТ: ОТЧЁТЫ ═══')
# ══════════════════════════════════════════════════════

send('18 Дайджест нод',
     R_CHAT, R_TOP,
     '📊 <b>Дайджест нод — 24.06.2026 09:00 UTC</b>\n\n'
     '🟢 <b>🇫🇷 France</b>  ● в пуле\n'
     '  score=0.056  ping=52ms  bw=1.3/712Мбит/с  spd=712Мбит/с\n'
     '  users=3  cpu=2.9%  ram=33.5%\n\n'
     '🟡 <b>🇫🇮 Finland</b>  ● в пуле\n'
     '  score=0.61  ping=117ms  bw=0.1/50Мбит/с  spd=22Мбит/с\n'
     '  users=8  cpu=13.5%  ram=59.4%')

send('19 Лог за сутки',
     R_CHAT, R_TOP,
     '📋 <b>Лог балансировщика — 2026-06-24</b>\n'
     '<pre>'
     '00:36 Finland: в пуле, score=0.097\n'
     '00:36 France:  в пуле, score=0.058\n'
     '02:12 Finland: score=0.61 users=8 | ping=117ms bw=0.1/50Мбит/с\n'
     '02:12 France:  score=0.056 users=3 | ping=52ms bw=1.3/712Мбит/с\n'
     '04:18 Finland: CPU 91% — выведена из пула\n'
     '04:18 Finland: CPU 13% — возвращена в пул\n'
     '06:05 France:  score=0.058 users=2 | ping=51ms bw=0.9/712Мбит/с\n'
     '08:00 Finland: speedtest 22 Мбит/с — медленная нода\n'
     '09:00 Дайджест отправлен'
     '</pre>')

print(f'\n✓ Отправлено: {results["ok"]}  ✗ Ошибок: {results["fail"]}')
print(f'Всего уведомлений: {results["ok"] + results["fail"]} из 19')
