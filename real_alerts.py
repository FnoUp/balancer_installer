#!/usr/bin/env python3
"""Отправляет реальные TG-уведомления с живыми метриками из Prometheus."""
import re, requests, time, json, datetime

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
PROM   = 'http://localhost:9090'
TOKEN  = cfg('REMNAWAVE_TOKEN')
API    = cfg('REMNAWAVE_API')

print(f'Bot: {BOT[:12]}...  M_CHAT={M_CHAT}/{M_TOP}  E_CHAT={E_CHAT}/{E_TOP}  R_CHAT={R_CHAT}/{R_TOP}')

def prom_q(q):
    try:
        r = requests.get(f'{PROM}/api/v1/query', params={'query': q}, timeout=5)
        res = r.json()['data']['result']
        return float(res[0]['value'][1]) if res else None
    except:
        return None

def send(chat, topic, text):
    p = {'chat_id': chat, 'text': text, 'parse_mode': 'HTML'}
    if topic:
        p['message_thread_id'] = topic
    try:
        r = requests.post(f'https://api.telegram.org/bot{BOT}/sendMessage', json=p, timeout=10)
        return 'OK' if r.status_code == 200 else f'ERR {r.status_code} {r.text[:80]}'
    except Exception as e:
        return f'FAIL {e}'

def fmt(v, suffix=''):
    return f'{v:.1f}{suffix}' if v is not None else '?'

def clamp(v, mx):
    return min(1.0, max(0.0, (v or 0) / mx)) if mx else 0.0

# Nodes from balancer
nodes_raw = re.findall(
    r'\{\s*"name"\s*:\s*"([^"]+)"[^}]*"host_uuid"\s*:\s*"([^"]+)"[^}]*"prom_instance"\s*:\s*"([^"]+)"[^}]*"net_device"\s*:\s*"([^"]+)"',
    src, re.DOTALL
)
print(f'Нод найдено: {len(nodes_raw)}: {[n[0] for n in nodes_raw]}')

# Users from /api/nodes
def get_users_map():
    try:
        r = requests.get(f'{API}/nodes',
                         headers={'Authorization': f'Bearer {TOKEN}'}, timeout=8)
        return {n['address']: n.get('usersOnline', 0) for n in r.json().get('response', [])}
    except Exception as e:
        print(f'  users_map error: {e}')
        return {}

def get_host_addr(uuid):
    try:
        r = requests.get(f'{API}/hosts',
                         headers={'Authorization': f'Bearer {TOKEN}'}, timeout=8)
        h = next((h for h in r.json().get('response', []) if h['uuid'] == uuid), None)
        return h['address'] if h else ''
    except:
        return ''

users_map = get_users_map()
print(f'users_map: {users_map}')

# Capacity logic (mirrors balancer)
def capacity(spd, p95, age_h):
    if spd and spd >= 100:
        return spd, f'speedtest {spd:.0f} Мбит/с'
    if spd and 0 < spd < 100:
        return 50.0, f'speedtest {spd:.0f} Мбит/с (штраф)'
    if p95 and age_h >= 24:
        return max(p95, 50.0), f'tx_p95 {p95:.1f} Мбит/с'
    if age_h < 24:
        return 100.0, 'grace (нода &lt;24ч)'
    return 50.0, 'нет данных, штраф'

# First seen
try:
    with open('/etc/vpn-balancer/node_first_seen.json') as f:
        first_seen = json.load(f)
except:
    first_seen = {}

# Per-node loop
digest_lines = []
now_str = datetime.datetime.utcnow().strftime('%d.%m.%Y %H:%M UTC')

for name, uuid, inst, dev in nodes_raw:
    ip = inst.split(':')[0]
    print(f'\n=== {name} ({ip}) ===')

    ping_ms = prom_q(f"vpn_node_ping_ms{{instance='{inst}'}}")
    ping_ok = prom_q(f"vpn_node_ping_ok{{instance='{inst}'}}")
    cpu     = prom_q(f"100-(avg(rate(node_cpu_seconds_total{{instance='{inst}',mode='idle'}}[5m]))*100)")
    ram     = prom_q(f"100*(1-node_memory_MemAvailable_bytes{{instance='{inst}'}}/node_memory_MemTotal_bytes{{instance='{inst}'}})")
    tx      = prom_q(f"rate(node_network_transmit_bytes_total{{instance='{inst}',device='{dev}'}}[5m])*8/1024/1024")
    spd     = prom_q(f"vpn_node_capacity_mbps{{instance='{inst}'}}")
    p95     = prom_q(f"quantile_over_time(0.95,rate(node_network_transmit_bytes_total{{instance='{inst}',device='{dev}'}}[5m])[24h:5m])*8/1024/1024")

    ts     = first_seen.get(uuid, time.time())
    age_h  = (time.time() - ts) / 3600
    cap, cap_reason = capacity(spd, p95, age_h)

    addr  = get_host_addr(uuid)
    users = users_map.get(addr, 0)

    forced = (ping_ok is not None and ping_ok < 1) or \
             (cpu is not None and cpu >= 90) or \
             (ram is not None and ram >= 90)

    if forced:
        score = 1.0
    else:
        pm = ping_ms or 0
        score = round(
            clamp(pm, 300)       * 0.25 +
            clamp(tx or 0, cap)  * 0.50 +
            clamp(users, 100)    * 0.15 +
            clamp(cpu or 0, 100) * 0.07 +
            clamp(ram or 0, 100) * 0.03,
            4
        )

    spd_str = f'{spd:.0f}' if spd else 'нет'
    detail  = (f'ping={fmt(ping_ms)}ms  bw={fmt(tx)}/{cap:.0f}Мбит/с  '
               f'users={users}  cpu={fmt(cpu)}%  ram={fmt(ram)}%')

    print(f'  ping={fmt(ping_ms)}ms ok={ping_ok}  cpu={fmt(cpu)}%  ram={fmt(ram)}%')
    print(f'  tx={fmt(tx)}  spd={spd_str}  p95={fmt(p95)}  age={age_h:.1f}h')
    print(f'  capacity={cap:.0f} ({cap_reason})  users={users}  score={score}')

    # ── Ping алерты ──────────────────────────────────────────────
    if ping_ok is not None and ping_ok < 1:
        r = send(M_CHAT, M_TOP,
            f'📡 <b>Пинг недоступен</b>\n'
            f'Нода: <b>{name}</b>\n77.88.8.8 не отвечает\n<code>{detail}</code>')
        print(f'  ping_fail → {r}')
    elif ping_ms and ping_ms > 300:
        r = send(M_CHAT, M_TOP,
            f'📡 <b>Пинг критически высокий</b>\n'
            f'Нода: <b>{name}</b>\nПинг: <code>{ping_ms:.0f}ms</code> (порог 300ms)\n<code>{detail}</code>')
        print(f'  ping_high → {r}')
    else:
        r = send(M_CHAT, M_TOP,
            f'✅ <b>Пинг в норме</b>\n'
            f'Нода: <b>{name}</b>  Пинг: <code>{fmt(ping_ms)}ms</code>')
        print(f'  ping_ok → {r}')
    time.sleep(0.4)

    # ── CPU/RAM алерты ───────────────────────────────────────────
    if cpu is not None and cpu >= 90:
        r = send(M_CHAT, M_TOP,
            f'⚠️ <b>CPU критично</b>\n'
            f'Нода: <b>{name}</b>\nCPU: <code>{cpu:.0f}%</code> (порог 90%)\n<code>{detail}</code>')
        print(f'  cpu_crit → {r}')
    else:
        r = send(M_CHAT, M_TOP,
            f'✅ <b>CPU в норме</b>\n'
            f'Нода: <b>{name}</b>  CPU: <code>{fmt(cpu)}%</code>')
        print(f'  cpu_ok → {r}')
    time.sleep(0.4)

    if ram is not None and ram >= 90:
        r = send(M_CHAT, M_TOP,
            f'⚠️ <b>RAM критично</b>\n'
            f'Нода: <b>{name}</b>\nRAM: <code>{ram:.0f}%</code> (порог 90%)\n<code>{detail}</code>')
        print(f'  ram_crit → {r}')
    else:
        r = send(M_CHAT, M_TOP,
            f'✅ <b>RAM в норме</b>\n'
            f'Нода: <b>{name}</b>  RAM: <code>{fmt(ram)}%</code>')
        print(f'  ram_ok → {r}')
    time.sleep(0.4)

    # ── Speedtest алерт ──────────────────────────────────────────
    if spd and 0 < spd < 100:
        r = send(M_CHAT, M_TOP,
            f'🐢 <b>Нода аномально медленная</b>\n'
            f'Нода: <b>{name}</b>\n'
            f'Speedtest upload: <code>{spd:.1f} Мбит/с</code> (порог 100)\n'
            f'Capacity: <code>{cap:.0f} Мбит/с</code> ({cap_reason})\n<code>{detail}</code>')
        print(f'  spd_low → {r}')
    time.sleep(0.4)

    # ── Переполненность ──────────────────────────────────────────
    if users >= 100:
        r = send(M_CHAT, M_TOP,
            f'👥 <b>Нода переполнена</b>\n'
            f'Нода: <b>{name}</b>\nПользователей: <code>{users}</code> (макс 100)\n<code>{detail}</code>')
        print(f'  users_full → {r}')
    time.sleep(0.4)

    # ── Статус ноды ──────────────────────────────────────────────
    if score >= 0.75:
        icon, state = '🔴', 'ВЫВЕДЕНА ИЗ ПУЛА'
    elif score >= 0.55:
        icon, state = '🟡', 'НАГРУЖЕНА (в пуле)'
    else:
        icon, state = '🟢', 'В ПУЛЕ'

    r = send(M_CHAT, M_TOP,
        f'{icon} <b>Нода {name}</b> — {state}\n'
        f'score=<code>{score}</code>\n'
        f'<code>{detail}</code>\n'
        f'capacity={cap:.0f} Мбит/с ({cap_reason})\n'
        f'speedtest={spd_str} Мбит/с  возраст={age_h:.0f}ч')
    print(f'  status → {r}')
    time.sleep(0.4)

    # Для дайджеста
    digest_lines.append(
        f'{icon} <b>{name}</b>\n'
        f'  score={score}  ping={fmt(ping_ms)}ms  bw={fmt(tx)}/{cap:.0f}Мбит/с  spd={spd_str}Мбит/с\n'
        f'  users={users}  cpu={fmt(cpu)}%  ram={fmt(ram)}%  age={age_h:.0f}ч'
    )

# ── Дайджест ─────────────────────────────────────────────────
print('\n=== Дайджест ===')
digest = f'📊 <b>Дайджест нод — {now_str}</b>\n\n' + '\n\n'.join(digest_lines)
r = send(R_CHAT, R_TOP, digest)
print(f'  digest → {r}')

print('\nГотово!')
