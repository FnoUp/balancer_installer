#!/usr/bin/env python3
"""
auto_add_node.py — запускается на панели
Автоматическое подключение уже присоединённой И УЖЕ НАСТРОЕННОЙ ноды
(config-profile и Reality-ключи на ней настроены вручную/заранее — скрипт
их не трогает и не создаёт новых). Скрипт создаёт только клиентские объекты:
  - реальный Host (привязан к уже существующему VLESS-инбаунду ноды)
  - виртуальный хост-держатель для device-route RU
  - JSON-шаблон подписки для виртуального хоста
  - регистрирует ноду в balancer.py (через add_node.apply_node_to_configs)

Режимы запуска:
  python3 auto_add_node.py           — добавить одну или несколько нод подряд
  python3 auto_add_node.py --remove  — полностью удалить ноду (Remnawave + balancer.py + Prometheus)
  python3 auto_add_node.py --audit   — найти осиротевшие объекты (шаблон без хоста, хост без ноды и т.п.)
"""

import ipaddress
import json
import os
import re
import socket
import subprocess
import sys
import urllib.error
import urllib.request

from add_node import (
    BALANCER_FILE, SVC_NAME, NODES_YML, DOCKER_YML, PING_YML,
    ask, ask_net_device, ask_pool_tag, apply_node_to_configs,
    info, success, warn, error, _remove_ip_from_yml,
)

# Общий "VirtualHost" config-profile (shadowsocks-инбаунд, ни на одну ноду
# не привязан — существует только чтобы через него делать device-route RU
# хосты-держатели) — уже создан вручную в Remnawave, этот скрипт его не
# создаёт и не трогает, только читает.
VIRTUAL_HOST_PROFILE_UUID = "ddced350-0041-4f72-985f-9eab95215366"
VIRTUAL_HOST_ADDRESS = "web.max.ru"  # тот же decoy-адрес, что у остальных Auto/device-route хостов

COUNTRY_FLAGS = {
    "FI": ("🇫🇮", "Finland"),
    "SE": ("🇸🇪", "Sweden"),
    "RU": ("🇷🇺", "Moscow"),
    "DE": ("🇩🇪", "Germany"),
    "FR": ("🇫🇷", "France"),
    "NL": ("🇳🇱", "Netherlands"),
    "US": ("🇺🇸", "USA"),
    "GB": ("🇬🇧", "UK"),
    "TR": ("🇹🇷", "Turkey"),
    "PL": ("🇵🇱", "Poland"),
}

# Два общих клиентских хоста-балансировщика — клиент подключается именно
# к ним, а injectHosts по tagRegex подставляет любую живую ноду с этим
# тегом (тег на реальном хосте ноды проставляет balancer.py по здоровью).
AUTO_POOL_HOSTS = {
    # Happ достаёт иконку сервера ТОЛЬКО из настоящего emoji-флага страны
    # в начале названия (https://www.happ.su/main/ru/dev-docs/emoji#flagi) —
    # проверено вживую: даже 🏳️ (белый флаг, не привязан к стране) иконкой
    # не становится. Так что просто emoji в названии как текст, без триггера.
    "BALANCER_WIFI":   ("🏠 Auto ДЛЯ ДОМАШНЕГО ИНТЕРНЕТА", "Balancer_Wifi"),
    "BALANCER_MOBILE": ("📱 Auto ДЛЯ МОБИЛЬНОГО ИНТЕРНЕТА", "Balancer_Mobile"),
}


def sanitize_name(s, fallback="node"):
    """subscription-template имена принимают только [A-Za-z0-9_\\s-],
    min_length=2 (проверено по исходникам remnawave/python-sdk) —
    remark хоста (флаг+страна) под это не подходит, чистим отдельно."""
    cleaned = re.sub(r'[^A-Za-z0-9_\s-]', '', s).strip()
    cleaned = re.sub(r'\s+', ' ', cleaned)
    if len(cleaned) < 2:
        cleaned = fallback
    return cleaned[:30]


_created = {}

def fail(msg):
    """Как error(), но дописывает список уже созданных объектов Remnawave
    (скрипт сам ничего не удаляет — только показывает, что осталось висеть)."""
    parts = [msg]
    if _created:
        parts.append("")
        parts.append("Уже создано (проверь/доразбери вручную в Remnawave):")
        for k, v in _created.items():
            parts.append(f"  {k}: {v}")
    error("\n".join(parts))


# ── SSH на ноду (список-аргументы, без двойного разбора шеллом) ────
def ssh_run(address, remote_cmd, timeout=20):
    try:
        result = subprocess.run(
            ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5",
             "-o", "StrictHostKeyChecking=accept-new", f"root@{address}", remote_cmd],
            capture_output=True, text=True, timeout=timeout,
        )
        return result.returncode == 0, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return False, "", "SSH timeout"


def preflight_ssh(address):
    info(f"Проверяем SSH-доступ на {address}...")
    ok, _, err = ssh_run(address, "true", timeout=10)
    if not ok:
        error(
            f"Нет SSH-доступа с панели на ноду {address}.\n"
            f"Выполни с панели: ssh-copy-id root@{address}\n"
            f"Ошибка: {err.strip()}"
        )
    success("SSH-доступ подтверждён")


# ── Remnawave API ────────────────────────────────────────────────
_rw_creds_cache = None

def rw_creds():
    global _rw_creds_cache
    if _rw_creds_cache is not None:
        return _rw_creds_cache
    with open(BALANCER_FILE) as f:
        content = f.read()
    api  = re.search(r'REMNAWAVE_API\s*=\s*"([^"]+)"', content)
    tok  = re.search(r'REMNAWAVE_TOKEN\s*=\s*"([^"]+)"', content)
    cook = re.search(r'REMNAWAVE_COOKIE\s*=\s*"([^"]+)"', content)
    if not api or not tok:
        error("Не удалось прочитать REMNAWAVE_API/REMNAWAVE_TOKEN из balancer.py")
    _rw_creds_cache = (api.group(1), tok.group(1), (cook.group(1) if cook else ""))
    return _rw_creds_cache


def rw_request(method, path, body=None):
    api, tok, cookie = rw_creds()
    headers = {"Authorization": f"Bearer {tok}", "Content-Type": "application/json"}
    if cookie:
        headers["Cookie"] = cookie
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"{api}{path}", data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"raw": raw.decode(errors="replace")}
    except Exception as e:
        return 0, {"error": str(e)}


def rw_get(path):
    status, data = rw_request("GET", path)
    if status != 200:
        fail(f"GET {path} -> HTTP {status}: {data}")
    return data.get("response", data)


# ── Шаг 1: обнаружение ноды ──────────────────────────────────────
def discover_nodes():
    nodes = rw_get("/nodes")
    hosts = rw_get("/hosts")
    existing_addrs = {h.get("address") for h in hosts if h.get("address")}

    if not nodes:
        error("В Remnawave вообще нет ни одной ноды (GET /nodes пуст)")

    print()
    print("=" * 60)
    print("   Автодобавление ноды — ноды, подключённые к Remnawave")
    print("=" * 60)
    print()
    for i, n in enumerate(nodes, 1):
        flag = COUNTRY_FLAGS.get(n.get("countryCode", ""), ("🏳️", n.get("countryCode", "?")))[0]
        conn = "подключена" if n.get("isConnected") else "НЕ подключена"
        has_profile = "есть config-profile" if (n.get("configProfile") or {}).get("activeConfigProfileUuid") else "БЕЗ config-profile"
        dup = "  [УЖЕ ЕСТЬ ХОСТ]" if n.get("address") in existing_addrs else ""
        print(f"    {i}) {flag} {n.get('name')}  ({n.get('address')})  — {conn}, {has_profile}{dup}")
    print()

    choice = ask("Номер ноды")
    try:
        idx = int(choice)
        if not (1 <= idx <= len(nodes)):
            raise ValueError
    except (ValueError, TypeError):
        error("Неверный номер ноды")
    node = nodes[idx - 1]

    if not node.get("isConnected"):
        warn(f"Нода {node.get('address')} сейчас НЕ подключена к Remnawave.")
        if ask("Всё равно продолжить? (y/n)", "n").lower() != "y":
            print("Отмена."); sys.exit(0)

    if not (node.get("configProfile") or {}).get("activeConfigProfileUuid"):
        error(
            f"У ноды {node.get('address')} нет привязанного config-profile.\n"
            f"Этот скрипт работает только с уже настроенными нодами — сначала настрой "
            f"config-profile и Reality-ключи на ней вручную в Remnawave."
        )

    if node.get("address") in existing_addrs:
        warn(f"У ноды {node.get('address')} уже есть хост в Remnawave — похоже, она уже настроена.")
        if ask("Всё равно продолжить обработку? (y/n)", "n").lower() != "y":
            print("Отмена."); sys.exit(0)

    return node


def find_vless_inbound(node):
    """Хост создаётся на уже существующем VLESS-инбаунде ноды — берём его
    напрямую из GET /nodes (activeInbounds), ничего заново не создаём.
    На бридж-нодах может быть ещё и shadowsocks-инбаунд — его пропускаем."""
    inbounds = (node.get("configProfile") or {}).get("activeInbounds") or []
    vless = [i for i in inbounds if i.get("type") == "vless"]
    if not vless:
        fail(
            f"У ноды нет активного VLESS-инбаунда (есть только: "
            f"{[i.get('type') for i in inbounds]}) — нечего использовать для хоста."
        )
    if len(vless) > 1:
        warn(f"У ноды несколько VLESS-инбаундов — беру первый: {vless[0].get('tag')}")
    return vless[0]["uuid"]


# ── Автоопределение домена/IP/интерфейса ─────────────────────────
def resolve_ip(address):
    try:
        ipaddress.ip_address(address)
        return address
    except ValueError:
        pass
    try:
        return socket.gethostbyname(address)
    except socket.gaierror:
        return None


def detect_interface(address, resolved_ip):
    """SSH на ноду, ищем единственный интерфейс с IP == resolved_ip.
    None, если не нашли ровно одно совпадение (NAT/floating IP и т.п.)."""
    if not resolved_ip:
        return None
    remote_cmd = (
        "ip -o link show | awk -F': ' '{print $2}' | "
        "grep -vE '^lo$|^docker|^veth|^br-|^virbr' | "
        "while read -r i; do "
        "ip -4 addr show $i 2>/dev/null | grep -oP '(?<=inet\\s)[0-9.]+' | awk -v n=$i '{print n, $0}'; "
        "done"
    )
    ok, out, _ = ssh_run(address, remote_cmd)
    if not ok:
        return None
    matches = []
    for line in out.strip().splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1] == resolved_ip:
            matches.append(parts[0])
    return matches[0] if len(matches) == 1 else None


def resolve_remark(node):
    code = node.get("countryCode", "")
    if code in COUNTRY_FLAGS:
        flag, name = COUNTRY_FLAGS[code]
        default = f"{flag} {name}"
    else:
        warn(f"Код страны '{code}' не в списке известных флагов.")
        default = code or node.get("name", "node")
    return ask("Имя хоста (remark)", default)


# ── hosts ──────────────────────────────────────────────────────
def create_real_host(node, profile_uuid, inbound_uuid, remark, pool_tag):
    # Видимый клиенту хост — обычная прямая точка входа на реальный
    # инбаунд ноды. Тег пула ставим сразу при создании (не ждём первый
    # цикл проверки здоровья в balancer.py) — нода сразу попадает в пул;
    # дальше balancer.py сам снимает/возвращает тег по здоровью, как обычно.
    hosts = rw_get("/hosts")
    dup = next((h for h in hosts if h.get("remark") == remark), None)
    if dup:
        warn(f"Хост с remark '{remark}' уже существует ({dup['uuid']}) — вероятно, две ноды в одной стране.")
        if ask("Всё равно создать ещё один с таким же именем? (y/n)", "n").lower() != "y":
            error("Отмена — выбери другое имя хоста (remark) и запусти скрипт заново.")

    # inbound вложен в отдельный объект (CreateHostInboundData в remnawave/python-sdk),
    # плоские configProfileUuid/configProfileInboundUuid на верхнем уровне не принимаются.
    payload = {
        "remark": remark[:40],
        "address": node["address"],
        "port": 443,
        "inbound": {
            "configProfileUuid": profile_uuid,
            "configProfileInboundUuid": inbound_uuid,
        },
        "fingerprint": "firefox",
        "tags": [pool_tag],
    }
    status, data = rw_request("POST", "/hosts", payload)
    if status not in (200, 201):
        fail(f"POST /hosts -> HTTP {status}: {data}")
    host = data.get("response", data)
    if "uuid" not in host:
        fail(f"POST /hosts вернул неожиданный формат ответа: {host}")
    _created["real-host"] = host["uuid"]
    success(f"Реальный хост создан: {host['uuid']} ({remark})")
    return host


def get_virtual_host_profile():
    """Общий decoy-профиль VirtualHost — используется ТОЛЬКО для авто-хостов
    WiFi/Mobile (у них нет одной конкретной ноды-владельца). Per-node хосты
    используют реальный профиль/инбаунд самой ноды, не этот."""
    status, data = rw_request("GET", "/config-profiles")
    profiles = (data.get("response", data) or {}).get("configProfiles", [])
    profile = next((p for p in profiles if p.get("uuid") == VIRTUAL_HOST_PROFILE_UUID), None)
    if not profile:
        fail(
            f"Не нашёл config-profile VirtualHost ({VIRTUAL_HOST_PROFILE_UUID}) в Remnawave — "
            f"проверь вручную, что он существует и его UUID не изменился."
        )
    inbounds = profile.get("inbounds") or []
    ss_inbound = next((i for i in inbounds if i.get("type") == "shadowsocks"), None)
    if not ss_inbound:
        fail(f"У config-profile VirtualHost нет shadowsocks-инбаунда (есть: {[i.get('type') for i in inbounds]})")
    return profile, ss_inbound


def create_virtual_holder_host(remark_base, real_host_uuid, node, profile_uuid, inbound_uuid):
    # Скрытый хост-держатель для device-route RU: не выбирается клиентом
    # напрямую (isHidden), существует только чтобы нести JSON-шаблон,
    # который инжектит по UUID видимый реальный хост (self-injection в
    # Remnawave запрещён — хост не может инжектить сам себя, поэтому нужен
    # отдельный объект). Профиль/инбаунд — тот же, что у самой ноды
    # (не decoy VirtualHost — тот только для авто-хостов WiFi/Mobile).
    remark = f"{remark_base} (VH device-route RU)"[:40]
    hosts = rw_get("/hosts")
    dup = next((h for h in hosts if h.get("remark") == remark), None)
    if dup:
        warn(f"Хост с remark '{remark}' уже существует ({dup['uuid']}).")
        if ask("Всё равно создать ещё один? (y/n)", "n").lower() != "y":
            return dup

    payload = {
        "remark": remark,
        "address": node["address"],
        "port": 443,
        "inbound": {
            "configProfileUuid": profile_uuid,
            "configProfileInboundUuid": inbound_uuid,
        },
        "fingerprint": "firefox",
        "isHidden": True,
    }
    status, data = rw_request("POST", "/hosts", payload)
    if status not in (200, 201):
        fail(f"POST /hosts (скрытый хост) -> HTTP {status}: {data}")
    host = data.get("response", data)
    if "uuid" not in host:
        fail(f"POST /hosts (скрытый хост) вернул неожиданный формат ответа: {host}")
    _created["virtual-host"] = host["uuid"]
    success(f"Скрытый хост-держатель создан: {host['uuid']} ({remark})")
    return host


# ── Подписные JSON-шаблоны (по образцу Templates/*.txt) ──
def _client_dns():
    return {
        "hosts": {
            "cloudflare-dns.com": "1.1.1.1",
            "dns.yandex.ru": "77.88.8.8",
            "dns.google": "8.8.8.8",
            "dns.quad9.net": "9.9.9.9",
        },
        "servers": [
            {"address": "https://dns.yandex.ru/dns-query",
             "domains": ["domain:ru", "domain:su", "domain:xn--p1ai"],
             "skipFallback": True},
            {"address": "https://cloudflare-dns.com/dns-query", "skipFallback": True},
        ],
        "queryStrategy": "UseIP",
    }


def _client_direct_rules():
    return [
        {"type": "field", "protocol": ["bittorrent"], "outboundTag": "direct"},
        {"type": "field", "domain": ["geosite:category-ads-all"], "outboundTag": "block"},
        {"type": "field", "domain": [
            "domain:ru", "domain:su", "domain:xn--p1ai",
            "domain:vk.com", "domain:vk.me", "domain:userapi.com",
            "domain:yandex.net", "domain:yandex.com", "domain:ya.ru",
            "domain:mail.ru", "domain:ok.ru", "domain:sberbank.ru", "domain:gosuslugi.ru",
        ], "outboundTag": "direct"},
        {"type": "field", "ip": [
            "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",
            "169.254.0.0/16", "224.0.0.0/4", "255.255.255.255",
        ], "outboundTag": "direct"},
    ]


def _client_inbounds():
    return [
        {"tag": "socks", "port": "10808", "listen": "127.0.0.1", "protocol": "socks",
         "settings": {"udp": True, "auth": "noauth"},
         "sniffing": {"enabled": True, "destOverride": ["http", "tls", "quic"]}},
        {"tag": "http", "port": "10809", "listen": "127.0.0.1", "protocol": "http",
         "settings": {"allowTransparent": False},
         "sniffing": {"enabled": True, "destOverride": ["http", "tls", "quic"]}},
    ]


def _client_outbounds():
    return [
        {"tag": "direct", "protocol": "freedom"},
        {"tag": "block", "protocol": "blackhole"},
    ]


def build_subscription_template(real_host_uuid):
    """Шаблон для ОДНОЙ конкретной ноды — инжектит по UUID реального хоста
    (по образцу Client_SingleNode_Template.txt)."""
    return {
        "dns": _client_dns(),
        "routing": {
            "rules": _client_direct_rules() + [
                {"type": "field", "network": "tcp,udp", "outboundTag": "proxy"},
            ],
            "domainMatcher": "hybrid",
            "domainStrategy": "IPIfNonMatch",
        },
        "inbounds": _client_inbounds(),
        "outbounds": _client_outbounds(),
        "remnawave": {
            "injectHosts": [{
                "selector": {"type": "uuids", "values": [real_host_uuid]},
                "tagPrefix": "proxy",
                "selectFrom": "ALL",
            }]
        },
    }


def build_pool_subscription_template(pool_tag, balancer_name):
    """Шаблон для авто-хоста балансировщика — инжектит ЛЮБОЙ хост с этим
    тегом (по образцу Templates/Balancer_Client_Template.txt), Xray сам
    случайно выбирает между всеми подставленными живыми нодами."""
    return {
        "dns": _client_dns(),
        "routing": {
            "rules": _client_direct_rules() + [
                {"type": "field", "network": "tcp,udp", "balancerTag": balancer_name},
            ],
            "balancers": [{"tag": balancer_name, "selector": ["proxy"], "strategy": {"type": "random"}}],
            "domainMatcher": "hybrid",
            "domainStrategy": "IPIfNonMatch",
        },
        "inbounds": _client_inbounds(),
        "outbounds": _client_outbounds(),
        "remnawave": {
            "injectHosts": [{
                "selector": {"type": "tagRegex", "pattern": pool_tag},
                "tagPrefix": "proxy",
                "selectFrom": "ALL",
            }]
        },
    }


def find_subscription_template_by_name(name):
    status, data = rw_request("GET", "/subscription-templates")
    templates = (data.get("response", data) or {}).get("templates", [])
    return next((t for t in templates if t.get("name") == name), None)


def create_subscription_template(name, template_json):
    """Remnawave иногда отвечает HTTP 500 на POST, хотя шаблон фактически
    создаётся (проверено вживую) — поэтому: (1) сначала проверяем, нет ли
    уже шаблона с таким именем (например, remark хоста совпал с уже
    существующим — тогда это настоящая проблема, а не ложный 500), и
    (2) если POST вернул ошибку, перепроверяем по имени перед тем как
    сдаться — вдруг он всё же создался."""
    safe_name = sanitize_name(name)
    dup = find_subscription_template_by_name(safe_name)
    if dup:
        fail(
            f"Шаблон подписки с именем '{safe_name}' уже существует ({dup['uuid']}) — "
            f"скорее всего, имя хоста совпадает с уже существующим. Выбери другое имя хоста."
        )

    status, data = rw_request("POST", "/subscription-templates",
                               {"name": safe_name, "templateType": "XRAY_JSON"})
    if status not in (200, 201):
        retry = find_subscription_template_by_name(safe_name)
        if not retry:
            fail(f"POST /subscription-templates -> HTTP {status}: {data}")
        warn(f"POST вернул HTTP {status}, но шаблон '{safe_name}' всё же создался ({retry['uuid']}) — продолжаю")
        tpl = retry
    else:
        tpl = data.get("response", data)
        if "uuid" not in tpl:
            fail(f"POST /subscription-templates вернул неожиданный формат ответа: {tpl}")
    tpl_uuid = tpl["uuid"]
    _created["subscription-template"] = tpl_uuid

    status, data = rw_request("PATCH", "/subscription-templates", {"uuid": tpl_uuid, "templateJson": template_json})
    if status != 200:
        fail(f"PATCH /subscription-templates -> HTTP {status}: {data}")
    success(f"Шаблон подписки создан и заполнен: {tpl_uuid}")
    return tpl_uuid


def attach_template_to_host(host_uuid, template_uuid):
    status, data = rw_request("PATCH", "/hosts", {"uuid": host_uuid, "xrayJsonTemplateUuid": template_uuid})
    if status != 200:
        fail(f"PATCH /hosts (привязка шаблона) -> HTTP {status}: {data}")
    success("Шаблон привязан")


def self_check(real_host_uuid, virtual_host_uuid, template_uuid, pool_tag):
    """Перечитываем созданные объекты через GET и сверяем ключевые поля —
    ловит случаи, когда POST/PATCH вернул 200/201, но объект на деле не
    такой, каким мы его создавали (уже бывало с subscription-templates,
    см. комментарий в create_subscription_template). Ничего не удаляет и
    не блокирует выполнение — только предупреждает."""
    ok = True
    hosts = rw_get("/hosts")
    real = next((h for h in hosts if h.get("uuid") == real_host_uuid), None)
    virt = next((h for h in hosts if h.get("uuid") == virtual_host_uuid), None)

    if not real:
        warn("Самопроверка: реальный хост не находится через GET /hosts")
        ok = False
    elif pool_tag not in (real.get("tags") or []):
        warn(f"Самопроверка: у реального хоста нет тега пула {pool_tag}")
        ok = False

    if not virt:
        warn("Самопроверка: скрытый хост не находится через GET /hosts")
        ok = False
    else:
        if not virt.get("isHidden"):
            warn("Самопроверка: скрытый хост не помечен isHidden")
            ok = False
        if virt.get("xrayJsonTemplateUuid") != template_uuid:
            warn("Самопроверка: у скрытого хоста не привязан созданный шаблон подписки")
            ok = False

    status, data = rw_request("GET", "/subscription-templates")
    templates = (data.get("response", data) or {}).get("templates", [])
    if not any(t.get("uuid") == template_uuid for t in templates):
        warn("Самопроверка: шаблон подписки не находится через GET /subscription-templates")
        ok = False

    if ok:
        success("Самопроверка пройдена — все объекты на месте и согласованы")
    else:
        warn("Самопроверка нашла расхождения — см. предупреждения выше, проверь вручную в Remnawave")
    return ok


def ensure_auto_pool_hosts():
    """Два общих клиентских авто-хоста (WiFi/Mobile), которые и отвечают за
    балансировку при подключении к ним — идемпотентно: если уже есть хост
    с таким remark, пропускает, ничего не дублирует."""
    hosts = rw_get("/hosts")
    for pool_tag, (remark, balancer_name) in AUTO_POOL_HOSTS.items():
        existing = next((h for h in hosts if h.get("remark") == remark), None)
        if existing:
            info(f"Автохост «{remark}» уже существует ({existing['uuid']}) — пропускаю")
            continue

        profile, ss_inbound = get_virtual_host_profile()
        payload = {
            "remark": remark,
            "address": VIRTUAL_HOST_ADDRESS,
            "port": ss_inbound["port"],
            "inbound": {
                "configProfileUuid": profile["uuid"],
                "configProfileInboundUuid": ss_inbound["uuid"],
            },
            "fingerprint": "firefox",
            "tags": [pool_tag],
        }
        status, data = rw_request("POST", "/hosts", payload)
        if status not in (200, 201):
            fail(f"POST /hosts (автохост {remark}) -> HTTP {status}: {data}")
        host = data.get("response", data)
        if "uuid" not in host:
            fail(f"POST /hosts (автохост {remark}) вернул неожиданный формат ответа: {host}")
        _created[f"auto-host-{pool_tag}"] = host["uuid"]
        success(f"Автохост «{remark}» создан: {host['uuid']}")

        tpl_json = build_pool_subscription_template(pool_tag, balancer_name)
        tpl_uuid = create_subscription_template(balancer_name, tpl_json)
        attach_template_to_host(host["uuid"], tpl_uuid)


# ── Удаление ноды (обратная операция) ────────────────────────────
def list_balancer_nodes():
    """Тот же паттерн парсинга NODES из balancer.py, что и в пункте
    «Исправить/удалить ноду» в balancer.sh — держим в одном месте логику,
    сверяться приходится вручную в обоих файлах при правке формата."""
    with open(BALANCER_FILE) as f:
        content = f.read()
    blocks = re.findall(r'\{[^{}]*\}', content, re.DOTALL)
    raw_nodes = [b for b in blocks if '"prom_instance"' in b]
    result = []
    for b in raw_nodes:
        def field(k):
            m = re.search(r'"' + k + r'"\s*:\s*"([^"]*)"', b)
            return m.group(1) if m else ""
        result.append({
            "name": field("name"),
            "host_uuid": field("host_uuid"),
            "prom_instance": field("prom_instance"),
            "pool_tag": field("pool_tag"),
        })
    return result


def remove_node_entry_from_balancer_py(ip):
    with open(BALANCER_FILE) as f:
        content = f.read()
    new_content = re.sub(
        r'\s*\{[^}]*"prom_instance":\s*"' + re.escape(ip) + r':[^}]*\},?',
        "", content, flags=re.DOTALL,
    )
    with open(BALANCER_FILE, "w") as f:
        f.write(new_content)


def remove_node():
    nodes = list_balancer_nodes()
    if not nodes:
        error("В balancer.py нет ни одной ноды (список NODES пуст)")

    print()
    print("=" * 60)
    print("   Удаление ноды — полностью (Remnawave + balancer.py + Prometheus)")
    print("=" * 60)
    print()
    for i, n in enumerate(nodes, 1):
        ip = n["prom_instance"].split(":")[0]
        print(f"    {i}) {n['name']}  IP={ip}  пул={n['pool_tag']}")
    print()

    choice = ask("Номер ноды для удаления")
    try:
        idx = int(choice)
        if not (1 <= idx <= len(nodes)):
            raise ValueError
    except (ValueError, TypeError):
        error("Неверный номер ноды")
    node = nodes[idx - 1]
    ip = node["prom_instance"].split(":")[0]

    hosts = rw_get("/hosts")
    real_host = next((h for h in hosts if h.get("uuid") == node["host_uuid"]), None)
    virtual_host = None
    if not real_host:
        warn(f"Хост {node['host_uuid']} не найден в Remnawave (уже удалён вручную?) — почищу только balancer.py/Prometheus")
    else:
        vh_remark = f"{real_host.get('remark', '')} (VH device-route RU)"[:40]
        virtual_host = next((h for h in hosts if h.get("remark") == vh_remark), None)
        if not virtual_host:
            warn(f"Скрытый хост-держатель «{vh_remark}» не найден — возможно, уже удалён")

    template_uuid = virtual_host.get("xrayJsonTemplateUuid") if virtual_host else None

    print()
    print("─" * 50)
    warn("Будет удалено:")
    print(f"    Нода:              {node['name']}  ({ip})")
    if real_host:
        print(f"    Реальный хост:     {real_host['uuid']} ({real_host.get('remark')})")
    if virtual_host:
        print(f"    Скрытый хост:      {virtual_host['uuid']} ({virtual_host.get('remark')})")
    if template_uuid:
        print(f"    Шаблон подписки:   {template_uuid}")
    print("    Запись в balancer.py и Prometheus targets")
    print("─" * 50)
    if ask("Подтвердить удаление? (yes/n)", "n").lower() != "yes":
        print("Отмена."); sys.exit(0)

    if template_uuid:
        status, data = rw_request("DELETE", f"/subscription-templates/{template_uuid}")
        success(f"Шаблон подписки удалён: {template_uuid}") if status == 200 \
            else warn(f"Не удалось удалить шаблон {template_uuid}: HTTP {status} {data}")

    if virtual_host:
        status, data = rw_request("DELETE", f"/hosts/{virtual_host['uuid']}")
        success(f"Скрытый хост удалён: {virtual_host['uuid']}") if status == 200 \
            else warn(f"Не удалось удалить скрытый хост: HTTP {status} {data}")

    if real_host:
        status, data = rw_request("DELETE", f"/hosts/{real_host['uuid']}")
        success(f"Реальный хост удалён: {real_host['uuid']}") if status == 200 \
            else warn(f"Не удалось удалить реальный хост: HTTP {status} {data}")

    remove_node_entry_from_balancer_py(ip)
    success("Запись удалена из balancer.py")

    n_removed = sum(_remove_ip_from_yml(p, ip) for p in (NODES_YML, DOCKER_YML, PING_YML))
    success(f"Удалено {n_removed} запис(ей) из Prometheus targets")

    subprocess.run(["systemctl", "restart", "prometheus"], check=False)
    subprocess.run(["systemctl", "restart", SVC_NAME], check=False)
    success(f"Prometheus и {SVC_NAME} перезапущены")

    print()
    print("=" * 60)
    success(f"Нода {node['name']} полностью удалена")
    print("=" * 60)
    print()


# ── Аудит: поиск осиротевших объектов ────────────────────────────
def audit():
    print()
    print("=" * 60)
    print("   Аудит — поиск осиротевших объектов в Remnawave")
    print("=" * 60)
    print()

    nodes = rw_get("/nodes")
    hosts = rw_get("/hosts")
    status, data = rw_request("GET", "/subscription-templates")
    templates = (data.get("response", data) or {}).get("templates", [])
    balancer_nodes = list_balancer_nodes()

    node_addrs = {n.get("address") for n in nodes}
    host_by_uuid = {h["uuid"]: h for h in hosts}
    templates_in_use = {h.get("xrayJsonTemplateUuid") for h in hosts if h.get("xrayJsonTemplateUuid")}
    hosts_addrs = {h.get("address") for h in hosts}

    issues = []

    for h in hosts:
        addr = h.get("address")
        if addr not in node_addrs and addr != VIRTUAL_HOST_ADDRESS:
            issues.append(
                f"Хост «{h.get('remark')}» ({h['uuid']}) указывает на адрес {addr}, "
                f"которого нет среди нод Remnawave (нода удалена/переехала?)"
            )

    for t in templates:
        # "Default" — встроенные глобальные фолбэк-шаблоны Remnawave (по одному
        # на каждый templateType: XRAY_JSON/MIHOMO/STASH/CLASH/SINGBOX),
        # к хосту никогда не привязываются по дизайну — не орфаны.
        if t.get("name") == "Default":
            continue
        if t["uuid"] not in templates_in_use:
            issues.append(f"Шаблон подписки «{t.get('name')}» ({t['uuid']}) не привязан ни к одному хосту")

    for n in balancer_nodes:
        if n["host_uuid"] and n["host_uuid"] not in host_by_uuid:
            issues.append(f"balancer.py: нода «{n['name']}» ссылается на несуществующий host_uuid {n['host_uuid']}")

    for n in nodes:
        has_profile = (n.get("configProfile") or {}).get("activeConfigProfileUuid")
        if has_profile and n.get("address") not in hosts_addrs:
            issues.append(
                f"Нода «{n.get('name')}» ({n.get('address')}) настроена (есть config-profile), "
                f"но для неё нет ни одного хоста — не онбордена через автодобавление"
            )

    if not issues:
        success("Осиротевших объектов не найдено — всё согласовано")
    else:
        warn(f"Найдено проблем: {len(issues)}")
        for i, msg in enumerate(issues, 1):
            print(f"  {i}. {msg}")
    print()


# ── main ────────────────────────────────────────────────────────
def onboard_one_node():
    _created.clear()  # трекер "уже создано" — только для текущей ноды

    node = discover_nodes()
    address = node["address"]

    preflight_ssh(address)

    profile_uuid = node["configProfile"]["activeConfigProfileUuid"]
    inbound_uuid = find_vless_inbound(node)

    pool_tag = ask_pool_tag()

    resolved_ip = resolve_ip(address)
    if not resolved_ip:
        error(f"Не удалось резолвить домен/IP ноды: {address}")

    net_dev = detect_interface(address, resolved_ip)
    if net_dev:
        success(f"Автоопределён интерфейс: {net_dev} (IP: {resolved_ip})")
    else:
        warn("Не удалось однозначно определить интерфейс автоматически (NAT/несколько адресов на ноде?)")
        net_dev = ask_net_device()

    remark = resolve_remark(node)

    print()
    print("─" * 50)
    info(f"Нода:          {node.get('name')} ({address})")
    info(f"Config-profile: {profile_uuid}  (уже настроен, не создаётся заново)")
    info(f"Пул:           {pool_tag}")
    info(f"Интерфейс:     {net_dev}")
    info(f"Имя хоста:     {remark}")
    print("─" * 50)
    if ask("Создать хост и шаблон подписки? (y/n)", "y").lower() != "y":
        print("Отмена этой ноды.")
        return

    real_host = create_real_host(node, profile_uuid, inbound_uuid, remark, pool_tag)
    virtual_host = create_virtual_holder_host(remark, real_host["uuid"], node, profile_uuid, inbound_uuid)
    template_json = build_subscription_template(real_host["uuid"])
    template_uuid = create_subscription_template(f"{remark}_Direct", template_json)
    attach_template_to_host(virtual_host["uuid"], template_uuid)

    apply_node_to_configs(
        node_name=node.get("name"), node_ip=resolved_ip, location=remark,
        net_dev=net_dev, host_uuid=real_host["uuid"], pool_tag=pool_tag, tg_name=remark,
    )

    self_check(real_host["uuid"], virtual_host["uuid"], template_uuid, pool_tag)

    print()
    print("=" * 60)
    success("Автодобавление ноды завершено")
    print("=" * 60)
    info(f"Реальный хост:     {real_host['uuid']}")
    info(f"Виртуальный хост:  {virtual_host['uuid']}")
    info(f"Шаблон подписки:   {template_uuid}")
    print()


def main():
    ensure_auto_pool_hosts()

    while True:
        onboard_one_node()
        if ask("Добавить ещё одну ноду? (y/n)", "n").lower() != "y":
            break

    print()
    success("Готово")
    print()


if __name__ == "__main__":
    if os.geteuid() != 0:
        error("Запусти от root: sudo python3 auto_add_node.py")

    if "--remove" in sys.argv:
        remove_node()
    elif "--audit" in sys.argv:
        audit()
    else:
        main()
