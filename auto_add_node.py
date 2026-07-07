#!/usr/bin/env python3
"""
auto_add_node.py — запускается на панели
Полная автоматизация подключения уже присоединённой к Remnawave ноды:
  - обнаруживает ноду через GET /api/nodes (без ручного ввода IP/UUID)
  - сама определяет домен/IP/сетевой интерфейс ноды (SSH + DNS)
  - генерирует Reality-ключи на ноде, собирает и привязывает config-profile
  - создаёт реальный Host + виртуальный хост-держатель для device-route RU
  - создаёт и привязывает JSON-шаблон подписки для виртуального хоста
  - регистрирует ноду в balancer.py (через add_node.apply_node_to_configs)
"""

import ipaddress
import json
import os
import re
import secrets
import socket
import subprocess
import sys
import urllib.error
import urllib.request

from add_node import (
    BALANCER_FILE,
    ask, ask_net_device, ask_pool_tag, apply_node_to_configs,
    info, success, warn, error,
)

# Общий VirtualHost-профиль/инбаунд, на котором уже висят device-route хосты
# (Finland/Sweden/Moscow, созданные вручную в этой сессии) — новые виртуальные
# хосты копируют address/port/fingerprint/профиль с этого образца.
REFERENCE_VIRTUAL_HOST_UUID = "b96e18db-5b11-427e-85bc-c259e3a818ee"  # Finland (device-route RU)

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

BRIDGE_SS_PORT = 9999


def sanitize_name(s, fallback="node"):
    """config-profile и subscription-template имена принимают только
    [A-Za-z0-9_\\s-], min_length=2 (проверено по исходникам remnawave/python-sdk) —
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

    print()
    print("=" * 60)
    print("   Автодобавление ноды — ноды, подключённые к Remnawave")
    print("=" * 60)
    print()
    for i, n in enumerate(nodes, 1):
        flag = COUNTRY_FLAGS.get(n.get("countryCode", ""), ("🏳️", n.get("countryCode", "?")))[0]
        conn = "подключена" if n.get("isConnected") else "НЕ подключена"
        dup = "  [УЖЕ ЕСТЬ ХОСТ]" if n.get("address") in existing_addrs else ""
        print(f"    {i}) {flag} {n.get('name')}  ({n.get('address')})  — {conn}{dup}")
    print()

    if not nodes:
        error("В Remnawave вообще нет ни одной ноды (GET /nodes пуст)")

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

    if node.get("address") in existing_addrs:
        warn(f"У ноды {node.get('address')} уже есть хост в Remnawave — похоже, она уже настроена.")
        if ask("Всё равно продолжить обработку? (y/n)", "n").lower() != "y":
            print("Отмена."); sys.exit(0)

    return node


# ── Шаг 2: тип ноды ──────────────────────────────────────────────
def ask_node_type():
    print()
    print("  Тип ноды:")
    print()
    print("    1) Простая (WARP)                              (по умолчанию)")
    print("    2) Мост — вход  (принимает клиентов, уходит на мост-выход)")
    print("    3) Мост — выход (принимает мост-вход, уходит через WARP)")
    print()
    choice = input("  Выбор (Enter = 1): ").strip() or "1"
    return {"1": "simple", "2": "bridge_in", "3": "bridge_out"}.get(choice, "simple")


def ask_bridge_out_target():
    print()
    info("Мост-вход подключается к уже настроенной ноде мост-выход по shadowsocks.")
    dest_address = ask("Адрес (домен/IP) ноды мост-выход")
    dest_password = ask("Shadowsocks-пароль этой ноды мост-выход")
    return dest_address, dest_password


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


# ── Reality-ключи ─────────────────────────────────────────────────
def generate_reality_keys(address):
    """Реальный формат вывода 'xray x25519' — 'Password (PublicKey): ...',
    не просто 'PublicKey:' — проверено вживую на реальной ноде."""
    info("Генерируем Reality-ключи на ноде...")
    ok, out, err = ssh_run(address, "docker run --rm teddysun/xray xray x25519", timeout=60)
    if not ok:
        error(f"Не удалось сгенерировать ключи на {address}: {err.strip()}")
    priv = re.search(r'Private[ _]?[Kk]ey[^:]*:\s*(\S+)', out)
    pub  = re.search(r'(?:Public[ _]?[Kk]ey|Password)[^:]*:\s*(\S+)', out)
    if not priv:
        error(f"Не удалось распарсить приватный ключ из вывода 'xray x25519':\n{out}")
    return priv.group(1), (pub.group(1) if pub else None)


# ── Сборка config-profile JSON (по образцу Templates/Node_*.txt) ──
def _base_routing_rules(inbound_tags_to_outbound):
    rules = [
        {"ip": ["geoip:private"], "type": "field", "outboundTag": "BLOCK"},
        {"type": "field", "protocol": ["bittorrent"], "outboundTag": "BLOCK"},
        {"type": "field", "domain": ["geosite:category-ads-all"], "outboundTag": "BLOCK"},
        {"type": "field", "domain": [
            "geosite:category-ru",
            "ext:geosite-freedomnet.dat:ru-all",
            "ext:geosite-freedomnet.dat:category-ru-core",
        ], "outboundTag": "DIRECT"},
        {"type": "field", "ip": [
            "geoip:ru",
            "ext:geoip-freedomnet.dat:direct-vk",
            "ext:geoip-freedomnet.dat:direct-yandex",
        ], "outboundTag": "DIRECT"},
    ]
    for tag, outbound in inbound_tags_to_outbound.items():
        rules.append({"type": "field", "inboundTag": [tag], "outboundTag": outbound})
    return rules


def make_tags(remark):
    """Remnawave требует глобально уникальные inbound-теги по всей панели
    (проверено вживую: POST /config-profiles -> 409 A113 при повторе тега) —
    нельзя использовать один и тот же тег для каждой новой ноды."""
    base = re.sub(r'[^A-Za-z0-9]', '', remark) or "node"
    base = base[:20]
    return {
        "inbound": f"{base}-vless",
        "ss_inbound": f"{base}-ssin",
        "ss_bridge": f"{base}-ssbridge",
    }


def _vless_inbound(private_key, domain, inbound_tag):
    return {
        "tag": inbound_tag,
        "port": 443,
        "protocol": "vless",
        "settings": {"clients": [], "decryption": "none"},
        "sniffing": {"enabled": True, "destOverride": ["http", "tls", "quic"]},
        "streamSettings": {
            "network": "tcp",
            "security": "reality",
            "realitySettings": {
                "dest": "/dev/shm/nginx.sock",
                "show": False,
                "xver": 1,
                "spiderX": "",
                "shortIds": [""],
                "privateKey": private_key,
                "serverNames": [domain],
            },
        },
    }


def _base_outbounds(domain_strategy):
    return [
        {"tag": "DIRECT", "protocol": "freedom"},
        {"tag": "BLOCK", "protocol": "blackhole"},
        {
            "tag": "warp-out",
            "protocol": "freedom",
            "settings": {"domainStrategy": domain_strategy},
            "streamSettings": {"sockopt": {"interface": "warp", "tcpFastOpen": True}},
        },
    ]


def build_config_profile(node_type, domain, private_key, tags, *, ss_password=None,
                          dest_ip=None, dest_password=None):
    inbound_tag = tags["inbound"]
    inbounds = [_vless_inbound(private_key, domain, inbound_tag)]

    if node_type == "simple":
        outbounds = _base_outbounds("UseIP")
        routing = _base_routing_rules({inbound_tag: "warp-out"})

    elif node_type == "bridge_out":
        ss_inbound_tag = tags["ss_inbound"]
        outbounds = _base_outbounds("UseIPv4")
        inbounds.append({
            "tag": ss_inbound_tag,
            "port": BRIDGE_SS_PORT,
            "listen": "0.0.0.0",
            "protocol": "shadowsocks",
            "settings": {
                "method": "chacha20-ietf-poly1305",
                "clients": [],
                "network": "tcp,udp",
                "password": ss_password,
            },
        })
        routing = _base_routing_rules({inbound_tag: "warp-out", ss_inbound_tag: "warp-out"})

    elif node_type == "bridge_in":
        ss_bridge_tag = tags["ss_bridge"]
        outbounds = _base_outbounds("UseIPv4")
        outbounds.append({
            "tag": ss_bridge_tag,
            "protocol": "shadowsocks",
            "settings": {"servers": [{
                "port": BRIDGE_SS_PORT,
                "method": "chacha20-ietf-poly1305",
                "address": dest_ip,
                "password": dest_password,
            }]},
            "streamSettings": {"sockopt": {"interface": "warp", "tcpFastOpen": True}},
        })
        routing = _base_routing_rules({inbound_tag: ss_bridge_tag})

    else:
        error(f"Неизвестный тип ноды: {node_type}")

    return {
        "log": {"loglevel": "warning"},
        "dns": {
            "servers": [{"address": "https://dns.google/dns-query", "skipFallback": False}],
            "queryStrategy": "UseIPv4",
        },
        "inbounds": inbounds,
        "outbounds": outbounds,
        "routing": {"rules": routing},
    }


# ── config-profiles / nodes ────────────────────────────────────────
def extract_inbounds(profile):
    for key in ("inbounds", "parsedInbounds"):
        val = profile.get(key)
        if isinstance(val, list) and val and "uuid" in val[0]:
            return val
    return None


def find_inbound_uuid(profile, tag):
    inbounds = extract_inbounds(profile)
    if not inbounds:
        fail(f"Не нашёл список inbound'ов с uuid в ответе config-profile (ключи ответа: {list(profile.keys())})")
    match = next((i for i in inbounds if i.get("tag") == tag), None)
    if not match:
        fail(f"Не нашёл inbound с tag={tag} среди: {[i.get('tag') for i in inbounds]}")
    return match["uuid"]


def create_config_profile(name, config_json):
    """POST /config-profiles требует name+config за один вызов (CreateConfigProfileRequestDto
    в remnawave/python-sdk) — отдельного PATCH-довеска не нужно."""
    safe_name = sanitize_name(name)
    status, data = rw_request("POST", "/config-profiles", {"name": safe_name, "config": config_json})
    if status not in (200, 201):
        fail(f"POST /config-profiles -> HTTP {status}: {data}")
    profile = data.get("response", data)
    if "uuid" not in profile:
        fail(f"POST /config-profiles вернул неожиданный формат ответа: {profile}")
    _created["config-profile"] = profile["uuid"]
    success(f"Config-profile создан: {profile['uuid']}")
    return profile


def bind_profile_to_node(node_uuid, profile_uuid, inbound_uuids):
    # Схема подтверждена по исходникам remnawave/python-sdk (UpdateNodeRequestDto ->
    # NodeConfigProfileRequestDto) и живым GET /nodes: вложенный объект "configProfile".
    payload = {
        "uuid": node_uuid,
        "configProfile": {
            "activeConfigProfileUuid": profile_uuid,
            "activeInbounds": inbound_uuids,
        },
    }
    print()
    warn("Единственный ранее не проверенный вживую вызов — PATCH /nodes:")
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    if ask("Выполнить? (y/n)", "n").lower() != "y":
        print("Отменено пользователем перед PATCH /nodes.")
        print(f"Config-profile уже создан: {profile_uuid}")
        sys.exit(0)

    status, data = rw_request("PATCH", "/nodes", payload)
    if status != 200:
        fail(f"PATCH /nodes вернул HTTP {status}: {data}\nНичего не применилось — можно повторить.")

    nodes = rw_get("/nodes")
    node = next((n for n in nodes if n.get("uuid") == node_uuid), None)
    bound_uuid = (node or {}).get("configProfile", {}).get("activeConfigProfileUuid")
    if not node or bound_uuid != profile_uuid:
        fail(
            "PATCH /nodes вернул 200, но повторный GET /nodes не подтверждает применение.\n"
            "Состояние ноды могло измениться частично — НЕ повторяй PATCH не разобравшись.\n"
            f"Зайди в Remnawave -> Nodes -> {node_uuid} и проверь/выбери профиль вручную."
        )
    _created["node-bound-profile"] = f"{profile_uuid} -> node {node_uuid}"
    success("Профиль привязан к ноде и подтверждён повторным GET")


def get_virtual_host_reference():
    hosts = rw_get("/hosts")
    ref = next((h for h in hosts if h.get("uuid") == REFERENCE_VIRTUAL_HOST_UUID), None)
    if not ref:
        fail(
            f"Не нашёл референсный виртуальный хост {REFERENCE_VIRTUAL_HOST_UUID} — "
            f"проверь вручную address/port/fingerprint/configProfileUuid для VirtualHost-профиля."
        )
    return ref


def create_real_host(node, profile_uuid, inbound_uuid, remark):
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


def create_virtual_holder_host(remark_base, real_host_uuid):
    remark = f"{remark_base} (device-route RU)"[:40]
    hosts = rw_get("/hosts")
    dup = next((h for h in hosts if h.get("remark") == remark), None)
    if dup:
        warn(f"Хост с remark '{remark}' уже существует ({dup['uuid']}).")
        if ask("Всё равно создать ещё один? (y/n)", "n").lower() != "y":
            return dup

    ref = get_virtual_host_reference()
    ref_inbound = ref.get("inbound") or {}
    ref_profile_uuid = ref_inbound.get("configProfileUuid")
    ref_inbound_uuid = ref_inbound.get("configProfileInboundUuid")
    if not ref.get("address") or not ref.get("port") or not ref_profile_uuid or not ref_inbound_uuid:
        fail(
            f"Референсный виртуальный хост {REFERENCE_VIRTUAL_HOST_UUID} неполный "
            f"(address/port/inbound) — проверь его вручную в Remnawave: {ref}"
        )
    payload = {
        "remark": remark,
        "address": ref["address"],
        "port": ref["port"],
        "inbound": {
            "configProfileUuid": ref_profile_uuid,
            "configProfileInboundUuid": ref_inbound_uuid,
        },
        "fingerprint": ref.get("fingerprint") or "firefox",
    }
    status, data = rw_request("POST", "/hosts", payload)
    if status not in (200, 201):
        fail(f"POST /hosts (виртуальный хост) -> HTTP {status}: {data}")
    host = data.get("response", data)
    if "uuid" not in host:
        fail(f"POST /hosts (виртуальный хост) вернул неожиданный формат ответа: {host}")
    _created["virtual-host"] = host["uuid"]
    success(f"Виртуальный хост-держатель создан: {host['uuid']} ({remark})")
    return host


# ── Подписной JSON-шаблон (по образцу Client_SingleNode_Template.txt) ──
def build_subscription_template(real_host_uuid):
    return {
        "dns": {
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
        },
        "routing": {
            "rules": [
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
                {"type": "field", "network": "tcp,udp", "outboundTag": "proxy"},
            ],
            "domainMatcher": "hybrid",
            "domainStrategy": "IPIfNonMatch",
        },
        "inbounds": [
            {"tag": "socks", "port": "10808", "listen": "127.0.0.1", "protocol": "socks",
             "settings": {"udp": True, "auth": "noauth"},
             "sniffing": {"enabled": True, "destOverride": ["http", "tls", "quic"]}},
            {"tag": "http", "port": "10809", "listen": "127.0.0.1", "protocol": "http",
             "settings": {"allowTransparent": False},
             "sniffing": {"enabled": True, "destOverride": ["http", "tls", "quic"]}},
        ],
        "outbounds": [
            {"tag": "direct", "protocol": "freedom"},
            {"tag": "block", "protocol": "blackhole"},
        ],
        "remnawave": {
            "injectHosts": [{
                "selector": {"type": "uuids", "values": [real_host_uuid]},
                "tagPrefix": "proxy",
                "selectFrom": "ALL",
            }]
        },
    }


def create_subscription_template(name, template_json):
    safe_name = sanitize_name(name)
    status, data = rw_request("POST", "/subscription-templates",
                               {"name": safe_name, "templateType": "XRAY_JSON"})
    if status not in (200, 201):
        fail(f"POST /subscription-templates -> HTTP {status}: {data}")
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
    success("Шаблон привязан к виртуальному хосту")


# ── main ────────────────────────────────────────────────────────
def main():
    if os.geteuid() != 0:
        error("Запусти от root: sudo python3 auto_add_node.py")

    node = discover_nodes()
    address = node["address"]

    preflight_ssh(address)

    node_type = ask_node_type()

    dest_ip = dest_password = ss_password = None
    if node_type == "bridge_out":
        ss_password = secrets.token_urlsafe(24)
        info(f"Сгенерирован shadowsocks-пароль моста: {ss_password}")
        info(f"Порт моста: {BRIDGE_SS_PORT} (понадобится при настройке моста-входа к этой ноде)")
    elif node_type == "bridge_in":
        dest_address, dest_password = ask_bridge_out_target()
        dest_ip = resolve_ip(dest_address)
        if not dest_ip:
            error(f"Не удалось резолвить адрес назначения моста: {dest_address}")

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
    info(f"Нода:       {node.get('name')} ({address})")
    info(f"Тип:        {node_type}")
    info(f"Пул:        {pool_tag}")
    info(f"Интерфейс:  {net_dev}")
    info(f"Имя хоста:  {remark}")
    print("─" * 50)
    if ask("Продолжить и сгенерировать ключи на ноде? (y/n)", "y").lower() != "y":
        print("Отмена."); sys.exit(0)

    priv_key, pub_key = generate_reality_keys(address)

    tags = make_tags(remark)
    config_json = build_config_profile(
        node_type, address, priv_key, tags,
        ss_password=ss_password, dest_ip=dest_ip, dest_password=dest_password,
    )

    profile = create_config_profile(f"{remark} (auto)", config_json)
    main_inbound_uuid = find_inbound_uuid(profile, tags["inbound"])
    all_inbound_uuids = [i["uuid"] for i in extract_inbounds(profile)]

    bind_profile_to_node(node["uuid"], profile["uuid"], all_inbound_uuids)

    real_host = create_real_host(node, profile["uuid"], main_inbound_uuid, remark)
    virtual_host = create_virtual_holder_host(remark, real_host["uuid"])
    template_json = build_subscription_template(real_host["uuid"])
    template_uuid = create_subscription_template(f"{remark}_Direct", template_json)
    attach_template_to_host(virtual_host["uuid"], template_uuid)

    apply_node_to_configs(
        node_name=node.get("name"), node_ip=resolved_ip, location=remark,
        net_dev=net_dev, host_uuid=real_host["uuid"], pool_tag=pool_tag, tg_name=remark,
    )

    print()
    print("=" * 60)
    success("Автодобавление ноды завершено")
    print("=" * 60)
    info(f"Config-profile:    {profile['uuid']}")
    info(f"Реальный хост:     {real_host['uuid']}")
    info(f"Виртуальный хост:  {virtual_host['uuid']}")
    info(f"Шаблон подписки:   {template_uuid}")
    if pub_key:
        info(f"Reality public key: {pub_key}")
    if node_type == "bridge_out":
        info(f"SS-пароль моста:   {ss_password}")
        info(f"SS-порт моста:     {BRIDGE_SS_PORT}")
    print()


if __name__ == "__main__":
    main()
