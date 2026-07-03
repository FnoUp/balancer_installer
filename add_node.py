#!/usr/bin/env python3
"""
add_node.py — запускается на панели (163.5.16.247)
Добавляет новую ноду в:
  - /etc/prometheus/targets/vpn_nodes.yml
  - /etc/prometheus/targets/docker.yml
  - /etc/prometheus/targets/ping.yml
  - /opt/vpn-balancer/balancer.py (список NODES)
Перезапускает Prometheus и vpn-balancer.
"""

import os
import re
import sys
import subprocess

# ── Пути ──────────────────────────────────────────────────────
TARGETS_DIR = "/etc/prometheus/targets"

# Читаем конфиг, созданный setup.sh
SVC_NAME = "vpn-balancer"
BALANCER_TAG = "BALANCER"
_config = "/etc/vpn-balancer/config"
if os.path.exists(_config):
    with open(_config) as _f:
        for _line in _f:
            if _line.startswith("SVC_NAME="):
                SVC_NAME = _line.strip().split("=", 1)[1]
            elif _line.startswith("BALANCER_TAG="):
                BALANCER_TAG = _line.strip().split("=", 1)[1]

BALANCER_FILE = f"/opt/{SVC_NAME}/balancer.py"
BALANCER_LOG  = f"/var/log/{SVC_NAME}/balancer.log"

NODES_YML  = f"{TARGETS_DIR}/vpn_nodes.yml"
DOCKER_YML = f"{TARGETS_DIR}/docker.yml"
PING_YML   = f"{TARGETS_DIR}/ping.yml"

# ── Цвета ─────────────────────────────────────────────────────
G  = "\033[0;32m"
Y  = "\033[1;33m"
B  = "\033[0;34m"
R  = "\033[0;31m"
NC = "\033[0m"

def info(msg):    print(f"{B}[INFO]{NC} {msg}")
def success(msg): print(f"{G}[OK]{NC} {msg}")
def warn(msg):    print(f"{Y}[WARN]{NC} {msg}")
def error(msg):   print(f"{R}[ERROR]{NC} {msg}"); sys.exit(1)

def ask(prompt, default=""):
    suffix = f" (Enter = {default})" if default else ""
    val = input(f"{prompt}{suffix}: ").strip()
    return val if val else default

def run(cmd):
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    return result.returncode == 0, result.stdout, result.stderr

def rw_get_host_remark(uuid):
    """Получаем remark хоста из Remnawave по UUID"""
    try:
        with open(BALANCER_FILE) as f:
            content = f.read()
        api  = re.search(r'REMNAWAVE_API\s*=\s*"([^"]+)"', content)
        tok  = re.search(r'REMNAWAVE_TOKEN\s*=\s*"([^"]+)"', content)
        cook = re.search(r'REMNAWAVE_COOKIE\s*=\s*"([^"]+)"', content)
        if not api or not tok:
            return None
        import urllib.request, json
        req = urllib.request.Request(
            f"{api.group(1)}/hosts",
            headers={
                "Authorization": f"Bearer {tok.group(1)}",
                **({"Cookie": cook.group(1)} if cook and cook.group(1) else {}),
            }
        )
        with urllib.request.urlopen(req, timeout=5) as r:
            hosts = json.loads(r.read()).get("response", [])
            host = next((h for h in hosts if h.get("uuid") == uuid), None)
            if host:
                return host.get("remark") or host.get("address") or host.get("name")
    except Exception:
        pass
    return None

# ── Проверки ──────────────────────────────────────────────────
if os.geteuid() != 0:
    error("Запусти от root: sudo python3 add_node.py")

if not os.path.exists(TARGETS_DIR):
    error(f"Директория {TARGETS_DIR} не найдена. Prometheus установлен?")

if not os.path.exists(BALANCER_FILE):
    error(f"Файл {BALANCER_FILE} не найден. Балансировщик установлен?")

# ── Приветствие ───────────────────────────────────────────────
print()
print("=" * 50)
print("   Добавление новой ноды в мониторинг")
print("=" * 50)
print()
print("Данные нужно взять из:")
print("  - IP ноды: вывод setup.sh (пункт 2 — установка на ноду)")
print("  - UUID хоста: Remnawave → Hosts → нужный хост → UUID")
print()

# ── Вопросы (с возможностью перевода) ────────────────────────
while True:
    node_name = ask("Название ноды (например: fr1, se1, de1)")
    if not node_name:
        print("Название не может быть пустым"); continue

    node_ip = ask("IP адрес ноды")
    if not node_ip:
        print("IP не может быть пустым"); continue
    if not re.match(r'^\d{1,3}(\.\d{1,3}){3}$', node_ip):
        print("Некорректный IP адрес"); continue

    location = ask("Локация (например: france, sweden, germany)")
    if not location:
        print("Локация не может быть пустой"); continue

    net_dev = ask("Сетевой интерфейс на ноде", "eth0")

    host_uuid = ask("UUID хоста в Remnawave (из панели → Hosts)")
    if not host_uuid:
        print("UUID не может быть пустым"); continue

    # Выбор пула
    print()
    print("  В какой пул добавить ноду?")
    print()
    print("    1) BALANCER        — основной трафик  (по умолчанию)")
    print("    2) BALANCER_WIFI   — WiFi-клиенты")
    print("    3) BALANCER_MOBILE — мобильные клиенты")
    print()
    while True:
        pool_input = input("  Выбор (Enter = 1): ").strip() or "1"
        if pool_input in ("1", "2", "3"):
            break
        print("  Введи 1, 2 или 3")
    pool_tag = {"1": "BALANCER", "2": "BALANCER_WIFI", "3": "BALANCER_MOBILE"}[pool_input]

    # Получаем remark из Remnawave автоматически
    rw_remark = rw_get_host_remark(host_uuid)
    if rw_remark:
        tg_name = rw_remark
        success(f"Имя из Remnawave: {tg_name}")
    else:
        tg_name = node_name
        warn(f"Не удалось получить remark из Remnawave, используем: {tg_name}")

    print()
    print("─" * 50)
    info(f"Имя:        {node_name}")
    info(f"IP:         {node_ip}")
    info(f"Локация:    {location}")
    info(f"Интерфейс:  {net_dev}")
    info(f"Host UUID:  {host_uuid}")
    info(f"Пул:        {pool_tag}")
    info(f"TG имя:     {tg_name}")
    print("─" * 50)
    print()

    confirm = input("Всё верно? (y = добавить, n = ввести заново, q = выйти): ").strip().lower()
    if confirm == "y":
        break
    elif confirm == "q":
        print("Отмена.")
        sys.exit(0)
    else:
        print()
        info("Вводим заново...")
        print()

print()

# ── Проверка дублей — авто-удаление старых записей ───────────
def _remove_ip_from_yml(path, ip):
    """Удаляет YAML-блоки содержащие ip из файла targets."""
    if not os.path.exists(path):
        return 0
    with open(path) as f:
        content = f.read()
    if ip not in content:
        return 0
    # Блок начинается с "- targets:" и длится до следующего такого же блока или EOF
    blocks = re.split(r'(?=^- targets:)', content, flags=re.MULTILINE)
    before = len(blocks)
    blocks = [b for b in blocks if ip not in b]
    with open(path, "w") as f:
        f.write("".join(blocks))
    return before - len(blocks)

def _remove_ip_from_balancer(path, ip):
    """Удаляет строку NODES с данным IP из balancer.py."""
    if not os.path.exists(path):
        return 0
    with open(path) as f:
        content = f.read()
    if ip not in content:
        return 0
    new_content = re.sub(
        r'\s*\{[^}]*"prom_instance":\s*"' + re.escape(ip) + r':[^}]*\},?',
        '',
        content,
        flags=re.DOTALL,
    )
    with open(path, "w") as f:
        f.write(new_content)
    return 1

_found_dup = any(
    os.path.exists(p) and node_ip in open(p).read()
    for p in [NODES_YML, DOCKER_YML, PING_YML, BALANCER_FILE]
    if os.path.exists(p)
)
if _found_dup:
    warn(f"IP {node_ip} уже найден в конфигах — старые записи будут удалены и заменены новыми.")
    for _path, _label in [(NODES_YML, "vpn_nodes.yml"), (DOCKER_YML, "docker.yml"),
                           (PING_YML, "ping.yml")]:
        _n = _remove_ip_from_yml(_path, node_ip)
        if _n:
            info(f"Удалено {_n} запис(ей) из {_label}")
    _n = _remove_ip_from_balancer(BALANCER_FILE, node_ip)
    if _n:
        info(f"Удалена запись из balancer.py")

# ── Проверка доступности ноды ─────────────────────────────────
info(f"Проверяем доступность {node_ip}:9100...")
ok, out, _ = run(f"curl -s --connect-timeout 5 http://{node_ip}:9100/metrics | head -1")
if ok and "HELP" in out:
    success("node_exporter отвечает")
else:
    warn(f"node_exporter на {node_ip}:9100 недоступен (проверь firewall или node_setup.sh)")

# ── vpn_nodes.yml ─────────────────────────────────────────────
info("Обновляем vpn_nodes.yml...")

new_node_entry = f"""
- targets:
    - "{node_ip}:9100"
  labels:
    name: "{node_name}"
    location: "{location}"
    role: vpn
"""

with open(NODES_YML, "a") as f:
    f.write(new_node_entry)

success(f"Добавлено в {NODES_YML}")

# ── docker.yml ────────────────────────────────────────────────
info("Обновляем docker.yml...")

new_docker_entry = f"""
- targets:
    - "{node_ip}:8080"
  labels:
    name: "{node_name}"
    role: docker
"""

with open(DOCKER_YML, "a") as f:
    f.write(new_docker_entry)

success(f"Добавлено в {DOCKER_YML}")

# ── ping.yml ──────────────────────────────────────────────────
info("Обновляем ping.yml...")

new_ping_entry = f"""
- targets:
    - "{node_ip}"
  labels:
    name: "{node_name}"
    location: "{location}"
    role: vpn
"""

with open(PING_YML, "a") as f:
    f.write(new_ping_entry)

success(f"Добавлено в {PING_YML}")

# ── balancer.py ───────────────────────────────────────────────
info("Добавляем ноду в balancer.py...")

new_node_code = f"""    {{
        "name":          "{tg_name}",
        "host_uuid":     "{host_uuid}",
        "prom_instance": "{node_ip}:9100",
        "ping_instance": "{node_ip}",
        "net_device":    "{net_dev}",
        "pool_tag":      "{pool_tag}",
    }},"""

with open(BALANCER_FILE, "r") as f:
    content = f.read()

# Вставляем перед закрывающей ] списка NODES
# Ищем последний элемент в NODES и вставляем после него
marker = "# Добавляй следующие ноды по аналогии:"
if marker in content:
    # Вставляем перед комментарием
    content = content.replace(
        marker,
        new_node_code + "\n    " + marker
    )
    with open(BALANCER_FILE, "w") as f:
        f.write(content)
    success(f"Нода добавлена в {BALANCER_FILE}")
else:
    warn("Не нашёл место для вставки в balancer.py — добавь вручную:")
    print(f"\n{new_node_code}\n")

# ── Проверка конфига Prometheus ───────────────────────────────
info("Проверяем конфиг Prometheus...")
ok, out, err = run("promtool check config /etc/prometheus/prometheus.yml")
if ok:
    success("Конфиг Prometheus валидный")
else:
    warn(f"Ошибка конфига: {err}")

# ── Перезапуск сервисов ───────────────────────────────────────
info("Перезапускаем Prometheus...")
ok, _, err = run("systemctl restart prometheus")
if ok:
    success("Prometheus перезапущен")
else:
    warn(f"Ошибка перезапуска Prometheus: {err}")

info(f"Перезапускаем {SVC_NAME}...")
ok, _, err = run(f"systemctl restart {SVC_NAME}")
if ok:
    success(f"{SVC_NAME} перезапущен")
else:
    warn(f"Ошибка перезапуска {SVC_NAME}: {err}")

# ── TG-уведомление ───────────────────────────────────────────
def _tg_notify_node_added():
    try:
        with open(BALANCER_FILE) as _f:
            _content = _f.read()
        _tok  = re.search(r'TG_BOT_TOKEN\s*=\s*"([^"]+)"', _content)
        _chat = re.search(r'TG_METRICS_CHAT_ID\s*=\s*"([^"]+)"', _content)
        _tpc  = re.search(r'TG_METRICS_TOPIC_ID\s*=\s*(\d+)', _content)
        if not _tok or not _chat or _tok.group(1).startswith("%%"):
            warn("TG-уведомление пропущено — токен не задан в balancer.py")
            return
        import json as _json, urllib.request as _req
        # Ping к ноде
        _ping = subprocess.run(
            ["ping", "-c", "3", "-q", node_ip], capture_output=True, text=True
        )
        _ping_line = next((l for l in _ping.stdout.splitlines() if "rtt" in l or "round-trip" in l), None)
        _ping_avg = "?"
        if _ping_line:
            _m = re.search(r'[\d.]+/([\d.]+)/', _ping_line)
            if _m:
                _ping_avg = f"{float(_m.group(1)):.0f}ms"
        _text = (
            f"🟢 <b>Новая нода подключена</b>\n"
            f"Нода: <b>{tg_name}</b>\n"
            f"IP: <code>{node_ip}</code>\n"
            f"Локация: {location}\n"
            f"Интерфейс: {net_dev}\n"
            f"Пинг (с панели): <code>{_ping_avg}</code>\n"
            f"Prometheus добавлен, балансировщик перезапущен"
        )
        _payload = {"chat_id": _chat.group(1), "text": _text, "parse_mode": "HTML"}
        if _tpc:
            _payload["message_thread_id"] = int(_tpc.group(1))
        _data = _json.dumps(_payload).encode()
        _req.urlopen(
            _req.Request(
                f"https://api.telegram.org/bot{_tok.group(1)}/sendMessage",
                data=_data, headers={"Content-Type": "application/json"}
            ), timeout=8
        )
        success("TG-уведомление отправлено")
    except Exception as _e:
        warn(f"TG-уведомление не отправлено: {_e}")

_tg_notify_node_added()

# ── Итог ──────────────────────────────────────────────────────
print()
print("=" * 50)
print(f"{G}   Нода {node_name} добавлена!{NC}")
print("=" * 50)
print()
print("Проверь через 1-2 минуты:")
print(f"  Prometheus targets: http://localhost:9090/targets")
print(f"  Балансировщик:      tail -f {BALANCER_LOG}")
print()
print(f"Тег {BALANCER_TAG} балансировщик проставит автоматически когда нода здорова.")
print()
