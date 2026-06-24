#!/usr/bin/env python3
"""Симуляция всех TG-уведомлений балансировщика."""
import re, requests, time

with open('/opt/vpn-balancer/balancer.py') as f:
    src = f.read()

def cfg(key):
    m = re.search(r'^' + key + r'\s*=\s*["\']([^"\']+)["\']', src, re.MULTILINE)
    return m.group(1) if m else ''

def cfg_int(key):
    m = re.search(r'^' + key + r'\s*=\s*(\d+)', src, re.MULTILINE)
    return m.group(1) if m else '0'

BOT    = cfg('TG_BOT_TOKEN')
M_CHAT = cfg('TG_METRICS_CHAT_ID')
M_TOP  = cfg_int('TG_METRICS_TOPIC_ID')
E_CHAT = cfg('TG_ERRORS_CHAT_ID')
E_TOP  = cfg_int('TG_ERRORS_TOPIC_ID')
R_CHAT = cfg('TG_REPORTS_CHAT_ID')
R_TOP  = cfg_int('TG_REPORTS_TOPIC_ID')

print(f'Bot: {BOT[:12]}...')
print(f'Метрики: chat={M_CHAT} topic={M_TOP}')
print(f'Критика: chat={E_CHAT} topic={E_TOP}')
print(f'Отчёты:  chat={R_CHAT} topic={R_TOP}')

def send(label, chat, topic, text):
    payload = {'chat_id': chat, 'text': text, 'parse_mode': 'HTML'}
    if topic and topic != '0':
        payload['message_thread_id'] = int(topic)
    try:
        r = requests.post(
            f'https://api.telegram.org/bot{BOT}/sendMessage',
            json=payload, timeout=10
        )
        status = 'OK' if r.status_code == 200 else f'ERR {r.status_code}: {r.json().get("description","")}'
    except Exception as e:
        status = f'FAIL {e}'
    print(f'  {label}: {status}')
    time.sleep(0.4)

NAME   = 'France ТЕСТ'
DETAIL = 'ping=52ms bw=2.1/712Mbps users=3 cpu=45.0% ram=72.0%'

print('\n=== ЧАТ: МЕТРИКИ ===')

send('01 Balancer запущен',
     M_CHAT, M_TOP,
     '\U0001f680 <b>VPN Balancer запущен</b>\nМониторинг нод активен.')

send('02 Пинг недоступен',
     M_CHAT, M_TOP,
     f'\U0001f4e1 <b>Пинг недоступен</b>\nНода: <b>{NAME}</b>\n77.88.8.8 не отвечает')

send('03 Пинг высокий',
     M_CHAT, M_TOP,
     f'\U0001f4e1 <b>Пинг критически высокий</b>\nНода: <b>{NAME}</b>\nПинг: <code>350ms</code> (порог 300ms)')

send('04 Пинг восстановился',
     M_CHAT, M_TOP,
     f'✅ <b>Пинг восстановился</b>\nНода: <b>{NAME}</b>\nПинг: <code>52ms</code>')

send('05 CPU критично',
     M_CHAT, M_TOP,
     f'⚠️ <b>CPU критично</b>\nНода: <b>{NAME}</b>\nCPU: <code>93%</code> (порог 90%)')

send('06 CPU восстановился',
     M_CHAT, M_TOP,
     f'✅ <b>CPU восстановился</b>\nНода: <b>{NAME}</b>\nCPU: <code>45%</code>')

send('07 RAM критично',
     M_CHAT, M_TOP,
     f'⚠️ <b>RAM критично</b>\nНода: <b>{NAME}</b>\nRAM: <code>91%</code> (порог 90%)')

send('08 RAM восстановилась',
     M_CHAT, M_TOP,
     f'✅ <b>RAM восстановился</b>\nНода: <b>{NAME}</b>\nRAM: <code>72%</code>')

send('09 Speedtest медленный',
     M_CHAT, M_TOP,
     f'\U0001f422 <b>Нода аномально медленная</b>\nНода: <b>{NAME}</b>\nSpeedtest upload: <code>22.0 Mbps</code> (порог 100 Mbps)\nПроверь тариф VPS или сетевой интерфейс')

send('10 Нода переполнена',
     M_CHAT, M_TOP,
     f'\U0001f465 <b>Нода переполнена</b>\nНода: <b>{NAME}</b>\nАктивных пользователей: <code>100</code> (макс 100)')

send('11 Выведена из пула',
     M_CHAT, M_TOP,
     f'\U0001f534 <b>Нода выведена из пула</b>\nНода: <b>{NAME}</b>\nScore: <code>0.81</code> (порог 0.75)\n<code>{DETAIL}</code>')

send('12 Возвращена в пул',
     M_CHAT, M_TOP,
     f'\U0001f7e2 <b>Нода возвращена в пул</b>\nНода: <b>{NAME}</b>\nScore: <code>0.48</code> (порог 0.55)\n<code>{DETAIL}</code>')

send('13 Новая нода добавлена',
     M_CHAT, M_TOP,
     '\U0001f7e2 <b>Новая нода подключена</b>\n'
     'Нода: <b>\U0001f1e9\U0001f1ea Germany (ТЕСТ)</b>\n'
     'IP: <code>1.2.3.4</code>\nЛокация: germany\n'
     'Интерфейс: eth0\nПинг (с панели): <code>28ms</code>\n'
     'Prometheus добавлен, балансировщик перезапущен')

print('\n=== ЧАТ: КРИТИКА ===')

send('14 Единственная нода перегружена',
     E_CHAT, E_TOP,
     f'⚠️ <b>Нода перегружена, единственная в пуле</b>\n'
     f'Нода: <b>{NAME}</b>\nScore: <code>0.82</code>\n<code>{DETAIL}</code>')

send('15 Все ноды выведены',
     E_CHAT, E_TOP,
     f'\U0001f6a8 <b>ВСЕ НОДЫ ВЫВЕДЕНЫ ИЗ ПУЛА</b>\n'
     f'Пользователи не могут подключиться!\nПоследняя: <b>{NAME}</b>')

print('\n=== ЧАТ: ОТЧЕТЫ ===')

send('16 Дайджест',
     R_CHAT, R_TOP,
     '\U0001f4ca <b>Дайджест нод — 24.06.2026 09:00 UTC</b>\n\n'
     '\U0001f7e2 <b>\U0001f1eb\U0001f1f7 France (ТЕСТ)</b>  ● в пуле\n'
     '  score=0.056  ping=52ms  bw=2.1/712Mbps  spd=712Mbps\n'
     '  users=3  cpu=3.1%  ram=33.7%\n\n'
     '\U0001f7e1 <b>\U0001f1eb\U0001f1ee Finland (ТЕСТ)</b>  ● в пуле\n'
     '  score=0.34  ping=63ms  bw=1.2/50Mbps  spd=22Mbps\n'
     '  users=1  cpu=19.3%  ram=61.4%')

send('17 Дейли лог',
     R_CHAT, R_TOP,
     '\U0001f4cb <b>Лог за 24.06.2026</b>\n'
     '<pre>00:36 Finland: в пуле\n'
     '00:36 France: в пуле\n'
     '00:52 Finland: score=0.097 users=1 | ping=34ms bw=3.2/50Mbps\n'
     '00:52 France: score=0.058 users=1 | ping=52ms bw=0.8/712Mbps\n'
     '01:04 Finland: score=0.112 users=2 | ping=63ms bw=2.0/50Mbps\n'
     '...</pre>')

print('\nГотово! Все 17 уведомлений отправлены.')
