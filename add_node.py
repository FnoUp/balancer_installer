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

    net_dev = ask("Сетевой интерфейс на ноде", "ens3")

    host_uuid = ask("UUID хоста в Remnawave (из панели → Hosts)")
    if not host_uuid:
        print("UUID не может быть пустым"); continue

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
