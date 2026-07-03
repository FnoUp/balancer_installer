#!/usr/bin/env python3
import requests, time, logging, sys, datetime, json, os
from pathlib import Path

PROMETHEUS_URL   = "http://localhost:9090"
REMNAWAVE_API    = "https://%%DOMAIN%%/api"
REMNAWAVE_TOKEN  = "%%RW_TOKEN%%"
REMNAWAVE_COOKIE = "%%RW_COOKIE%%"

TG_BOT_TOKEN        = "%%TG_TOKEN%%"
TG_METRICS_CHAT_ID  = "%%TG_METRICS_CHAT%%"
TG_METRICS_TOPIC_ID = %%TG_METRICS_TOPIC%%
TG_ERRORS_CHAT_ID   = "%%TG_ERRORS_CHAT%%"
TG_ERRORS_TOPIC_ID  = %%TG_ERRORS_TOPIC%%
TG_REPORTS_CHAT_ID  = "%%TG_REP_CHAT%%"
TG_REPORTS_TOPIC_ID = %%TG_REP_TOPIC%%

BALANCER_NAME  = "%%BALANCER_NAME%%"
BALANCER_TAG   = "%%BALANCER_TAG%%"
LOG_FILE       = "/var/log/%%SVC_NAME%%/balancer.log"

CHECK_INTERVAL = 120
DIGEST_HOUR    = 9
ALERT_COOLDOWN = 1800

SCORE_BAD  = 0.75
SCORE_GOOD = 0.55

CPU_CRITICAL    = 90.0
RAM_CRITICAL    = 90.0
MAX_PING_MS     = 300.0
MAX_USERS       = 100
CAPACITY_FLOOR    = 50.0   # floor-штраф при плохом/отсутствующем speedtest (нода ≥ 24ч)
CAPACITY_GRACE    = 100.0  # floor для новых нод (< 24ч, нет speedtest и нет tx_p95)
MIN_SPEEDTEST     = 100.0  # ниже этого → штраф capacity=50 + алерт
SPEEDTEST_WARN    = 100.0  # порог для TG-алерта
NODE_GRACE_PERIOD = 86400  # секунд = 24ч grace для новых нод
FIRST_SEEN_FILE   = "/etc/vpn-balancer/node_first_seen.json"

W_PING = 0.25
W_BW   = 0.50
W_USER = 0.15
W_CPU  = 0.07
W_RAM  = 0.03

NODES = [
    # Добавляй следующие ноды по аналогии:
]

Path(LOG_FILE).parent.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("balancer")

node_state      = {}
node_alerts     = {}
last_prom_alert = 0
last_api_alert  = 0
last_digest_day = -1
_hosts_cache    = {"data": [], "ts": 0.0}
_nodes_cache    = {"data": [], "ts": 0.0}

def _load_first_seen():
    try:
        with open(FIRST_SEEN_FILE) as _f:
            return json.load(_f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def _save_first_seen(data):
    os.makedirs(os.path.dirname(FIRST_SEEN_FILE), exist_ok=True)
    with open(FIRST_SEEN_FILE, "w") as _f:
        json.dump(data, _f)

_first_seen = _load_first_seen()

# ── Telegram ───────────────────────────────────────────────────

def tg_send(chat_id, topic_id, text):
    try:
        payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
        if topic_id:
            payload["message_thread_id"] = topic_id
        requests.post(
            f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage",
            json=payload, timeout=10,
        )
    except Exception as e:
        log.warning(f"TG send failed: {e}")

_HDR = f"⚙️ <b>{BALANCER_NAME}</b>\n"

def tg_metrics(text):  tg_send(TG_METRICS_CHAT_ID, TG_METRICS_TOPIC_ID, _HDR + text)
def tg_critical(text): tg_send(TG_ERRORS_CHAT_ID,  TG_ERRORS_TOPIC_ID,  _HDR + text)
def tg_report(text):   tg_send(TG_REPORTS_CHAT_ID,  TG_REPORTS_TOPIC_ID, _HDR + text)

def node_can_alert(uuid, key, cooldown=ALERT_COOLDOWN):
    now = time.time()
    node_alerts.setdefault(uuid, {})
    if now - node_alerts[uuid].get(key, 0) > cooldown:
        node_alerts[uuid][key] = now
        return True
    return False

def node_reset_alert(uuid, key):
    node_alerts.setdefault(uuid, {})[key] = 0

# ── Prometheus ─────────────────────────────────────────────────

def prom_query(q):
    global last_prom_alert
    try:
        r = requests.get(f"{PROMETHEUS_URL}/api/v1/query", params={"query": q}, timeout=10)
        results = r.json()["data"]["result"]
        return float(results[0]["value"][1]) if results else None
    except Exception as e:
        log.warning(f"Prometheus query failed [{q[:60]}]: {e}")
        now = time.time()
        if now - last_prom_alert > ALERT_COOLDOWN:
            last_prom_alert = now
            tg_metrics(f"❌ <b>Prometheus недоступен</b>\nМетрики не читаются\n<code>{e}</code>")
        return None

# ── Remnawave API ──────────────────────────────────────────────

def _fetch_hosts():
    """GET /api/hosts с кешем 90 сек."""
    now = time.time()
    if now - _hosts_cache["ts"] < 90 and _hosts_cache["data"]:
        return _hosts_cache["data"]
    try:
        r = requests.get(
            f"{REMNAWAVE_API}/hosts",
            headers={"Authorization": f"Bearer {REMNAWAVE_TOKEN}", "Cookie": REMNAWAVE_COOKIE},
            timeout=10,
        )
        if r.status_code == 200:
            data = r.json().get("response", [])
            _hosts_cache["data"] = data
            _hosts_cache["ts"]   = now
            return data
    except Exception as e:
        log.warning(f"fetch_hosts error: {e}")
    return _hosts_cache["data"]

def _fetch_nodes():
    """GET /api/nodes с кешем 90 сек — содержит usersOnline на каждой ноде."""
    now = time.time()
    if now - _nodes_cache["ts"] < 90 and _nodes_cache["data"]:
        return _nodes_cache["data"]
    try:
        r = requests.get(
            f"{REMNAWAVE_API}/nodes",
            headers={"Authorization": f"Bearer {REMNAWAVE_TOKEN}", "Cookie": REMNAWAVE_COOKIE},
            timeout=10,
        )
        if r.status_code == 200:
            data = r.json().get("response", [])
            _nodes_cache["data"] = data
            _nodes_cache["ts"]   = now
            return data
    except Exception as e:
        log.warning(f"fetch_nodes error: {e}")
    return _nodes_cache["data"]

def set_host_tag(host_uuid, tag):
    global last_api_alert
    try:
        r = requests.patch(
            f"{REMNAWAVE_API}/hosts",
            headers={
                "Authorization": f"Bearer {REMNAWAVE_TOKEN}",
                "Cookie":        REMNAWAVE_COOKIE,
                "Content-Type":  "application/json",
            },
            json={"uuid": host_uuid, "tag": tag},
            timeout=15,
        )
        if r.status_code != 200:
            raise Exception(f"HTTP {r.status_code}: {r.text[:120]}")
        return True
    except Exception as e:
        log.error(f"Remnawave set_host_tag error: {e}")
        now = time.time()
        if now - last_api_alert > ALERT_COOLDOWN:
            last_api_alert = now
            tg_metrics(f"❌ <b>Ошибка API Remnawave</b>\nНе удалось обновить тег хоста\n<code>{e}</code>")
        return False

def get_active_users(host_uuid):
    """usersOnline берём из /api/nodes, сопоставляя по address хоста."""
    host = next((h for h in _fetch_hosts() if h.get("uuid") == host_uuid), None)
    if not host:
        return None
    host_addr = host.get("address", "")
    node = next((n for n in _fetch_nodes() if n.get("address", "") == host_addr), None)
    if node:
        val = node.get("usersOnline")
        return int(val) if val is not None else None
    return None

# ── Метрики ────────────────────────────────────────────────────

def clamp(val, lo=0.0, hi=1.0):
    return max(lo, min(hi, val))

def get_metrics(node, node_age_s=0):
    inst = node["prom_instance"]
    ping = node["ping_instance"]
    dev  = node["net_device"]

    # Доступность ноды — blackbox с панели к IP ноды
    probe_ok = prom_query(f"probe_success{{job='vpn_ping',instance='{ping}'}}")

    # Пинг с ноды до 77.88.8.8 (textfile_collector каждые 5 мин)
    ping_ms = prom_query(f"vpn_node_ping_ms{{instance='{inst}'}}")
    ping_ok = prom_query(f"vpn_node_ping_ok{{instance='{inst}'}}")

    # CPU и RAM
    cpu_pct = prom_query(
        f"100-(avg(rate(node_cpu_seconds_total{{instance='{inst}',mode='idle'}}[5m]))*100)"
    )
    ram_pct = prom_query(
        f"100*(1-node_memory_MemAvailable_bytes{{instance='{inst}'}}"
        f"/node_memory_MemTotal_bytes{{instance='{inst}'}})"
    )

    # Bandwidth — tx прямо сейчас
    tx_mbps = prom_query(
        f"rate(node_network_transmit_bytes_total{{instance='{inst}',device='{dev}'}}[5m])*8/1024/1024"
    )

    # Bandwidth — 95-й перцентиль за 24ч (реальный потолок без пиков)
    tx_p95 = prom_query(
        f"quantile_over_time(0.95,"
        f"rate(node_network_transmit_bytes_total{{instance='{inst}',device='{dev}'}}[5m])"
        f"[24h:5m])*8/1024/1024"
    )

    # Speedtest — ёмкость канала (обновляется каждые 8ч)
    speedtest_mbps = prom_query(f"vpn_node_capacity_mbps{{instance='{inst}'}}")

    # Нода недоступна через blackbox — возвращаем аварийный набор
    if probe_ok is not None and probe_ok < 1:
        return {
            "ping_ms":       9999.0,
            "ping_ok":       0.0,
            "cpu_pct":       cpu_pct or 0.0,
            "ram_pct":       ram_pct or 0.0,
            "tx_mbps":       tx_mbps or 0.0,
            "capacity":      CAPACITY_FLOOR,
            "speedtest_mbps": speedtest_mbps or 0.0,
        }

    # Нет основных метрик — пропускаем итерацию
    if None in (cpu_pct, ram_pct, tx_mbps):
        return None

    # capacity:
    #   speedtest >= 100          → speedtest (реальный потолок)
    #   speedtest < 100           → 50 (штраф за медленный VPS)
    #   нет speedtest + tx_p95>0 + нода ≥ 24ч → tx_p95 (реальный трафик)
    #   нет speedtest + нода < 24ч             → 100 (grace, нода только добавлена)
    #   нет данных + нода ≥ 24ч               → 50 (штраф)
    if speedtest_mbps:
        capacity = speedtest_mbps if speedtest_mbps >= MIN_SPEEDTEST else CAPACITY_FLOOR
    elif tx_p95 and node_age_s >= NODE_GRACE_PERIOD:
        capacity = max(tx_p95, CAPACITY_FLOOR)
    elif node_age_s < NODE_GRACE_PERIOD:
        capacity = CAPACITY_GRACE
    else:
        capacity = CAPACITY_FLOOR

    # ping_ok: textfile (нода→77.88.8.8) > blackbox probe > 1.0 (нет данных — не штрафуем)
    if ping_ok is not None:
        eff_ping_ok = ping_ok
        eff_ping_ms = ping_ms if ping_ms is not None else 9999.0
    elif probe_ok is not None and probe_ok < 1:
        eff_ping_ok = 0.0
        eff_ping_ms = 9999.0
    else:
        # данных ещё нет (нода только добавлена) — не штрафуем
        eff_ping_ok = 1.0
        eff_ping_ms = ping_ms if ping_ms is not None else 0.0

    return {
        "ping_ms":        eff_ping_ms,
        "ping_ok":        eff_ping_ok,
        "cpu_pct":        cpu_pct,
        "ram_pct":        ram_pct,
        "tx_mbps":        tx_mbps,
        "capacity":       capacity,
        "speedtest_mbps": speedtest_mbps or 0.0,
    }

def calc_score(m, active_users=0):
    if m["ping_ok"] < 1:
        return 1.0, "ping FAIL"
    if m["cpu_pct"] >= CPU_CRITICAL:
        return 1.0, f"CPU CRITICAL {m['cpu_pct']:.0f}%"
    if m["ram_pct"] >= RAM_CRITICAL:
        return 1.0, f"RAM CRITICAL {m['ram_pct']:.0f}%"

    score = (
        clamp(m["ping_ms"] / MAX_PING_MS)                * W_PING +
        clamp(m["tx_mbps"] / max(m["capacity"], 1.0))    * W_BW   +
        clamp((active_users or 0) / MAX_USERS)           * W_USER +
        clamp(m["cpu_pct"] / 100.0)                      * W_CPU  +
        clamp(m["ram_pct"] / 100.0)                      * W_RAM
    )
    detail = (
        f"ping={m['ping_ms']:.0f}ms "
        f"bw={m['tx_mbps']:.1f}/{m['capacity']:.0f}Мбит/с "
        f"users={active_users or 0} "
        f"cpu={m['cpu_pct']:.1f}% ram={m['ram_pct']:.1f}%"
    )
    return round(score, 4), detail

# ── Дайджест ───────────────────────────────────────────────────

def send_daily_digest():
    date_str = datetime.datetime.utcnow().strftime("%d.%m.%Y %H:%M UTC")
    lines = [f"📊 <b>Дайджест нод — {date_str}</b>\n"]
    for node in NODES:
        name    = node["name"]
        uuid    = node["host_uuid"]
        in_pool = node_state.get(uuid, False)
        status  = "● в пуле" if in_pool else "○ вне пула"
        users   = get_active_users(uuid) or 0
        age_s   = time.time() - _first_seen.get(uuid, time.time())
        m = get_metrics(node, age_s)
        if m is None:
            lines.append(f"<b>{name}</b>  {status}\n  ⚠️ метрики недоступны\n")
            continue
        score, _ = calc_score(m, users)
        icon = "🟢" if score < SCORE_GOOD else ("🟡" if score < SCORE_BAD else "🔴")
        spd = f"{m['speedtest_mbps']:.0f}" if m["speedtest_mbps"] > 0 else "нет"
        lines.append(
            f"{icon} <b>{name}</b>  {status}\n"
            f"  score={score}  ping={m['ping_ms']:.0f}ms  "
            f"bw={m['tx_mbps']:.1f}/{m['capacity']:.0f}Mbps  spd={spd}Mbps\n"
            f"  users={users}  cpu={m['cpu_pct']:.1f}%  ram={m['ram_pct']:.1f}%\n"
        )
    tg_report("\n".join(lines))
    log.info("Дайджест отправлен")

def send_daily_log():
    try:
        today    = datetime.datetime.utcnow().strftime("%Y-%m-%d")
        log_path = Path(LOG_FILE)
        if not log_path.exists():
            return
        lines = [l.strip() for l in log_path.read_text(errors="replace").splitlines() if today in l]
        if not lines:
            return
        recent = lines[-50:]
        text = f"📋 <b>Лог балансировщика — {today}</b>\n<pre>" + "\n".join(recent) + "</pre>"
        if len(text) > 4000:
            text = text[:3950] + "\n…</pre>"
        tg_report(text)
        log.info("Дейли лог отправлен")
    except Exception as e:
        log.error(f"send_daily_log error: {e}")

# ── Проверка ноды ──────────────────────────────────────────────

def check_node(node, nodes_in_pool):
    name     = node["name"]
    uuid     = node["host_uuid"]
    node_tag = node.get("pool_tag", BALANCER_TAG)
    in_pool  = node_state.get(uuid, True)

    # Определяем возраст ноды (сколько секунд она в системе)
    if uuid not in _first_seen:
        _first_seen[uuid] = time.time()
        _save_first_seen(_first_seen)
    node_age_s = time.time() - _first_seen[uuid]

    active_users = get_active_users(uuid)
    metrics      = get_metrics(node, node_age_s)

    if metrics is None:
        log.error(f"{name}: не удалось получить метрики")
        return

    m = metrics
    score, detail = calc_score(m, active_users)
    log.info(f"{name} [{node_tag}]: score={score} users={'?' if active_users is None else active_users} | {detail}")

    # ── Алерты ──────────────────────────────────────────────────

    # Пинг
    if m["ping_ok"] < 1:
        if node_can_alert(uuid, "ping_fail"):
            tg_metrics(f"📡 <b>Пинг недоступен</b>\nНода: <b>{name}</b>\n77.88.8.8 не отвечает")
    elif m["ping_ms"] > MAX_PING_MS:
        if node_can_alert(uuid, "ping_high"):
            tg_metrics(
                f"📡 <b>Пинг критически высокий</b>\nНода: <b>{name}</b>\n"
                f"Пинг: <code>{m['ping_ms']:.0f}ms</code> (порог {MAX_PING_MS:.0f}ms)"
            )
    else:
        was_bad = node_alerts.get(uuid, {}).get("ping_fail", 0) + node_alerts.get(uuid, {}).get("ping_high", 0)
        if was_bad > 0:
            node_reset_alert(uuid, "ping_fail")
            node_reset_alert(uuid, "ping_high")
            tg_metrics(f"✅ <b>Пинг восстановился</b>\nНода: <b>{name}</b>\nПинг: <code>{m['ping_ms']:.0f}ms</code>")

    # CPU
    if m["cpu_pct"] >= CPU_CRITICAL:
        if node_can_alert(uuid, "cpu_crit"):
            tg_metrics(
                f"⚠️ <b>CPU критично</b>\nНода: <b>{name}</b>\n"
                f"CPU: <code>{m['cpu_pct']:.0f}%</code> (порог {CPU_CRITICAL:.0f}%)"
            )
    elif node_alerts.get(uuid, {}).get("cpu_crit", 0) > 0 and m["cpu_pct"] < CPU_CRITICAL - 5:
        node_reset_alert(uuid, "cpu_crit")
        tg_metrics(f"✅ <b>CPU восстановился</b>\nНода: <b>{name}</b>\nCPU: <code>{m['cpu_pct']:.0f}%</code>")

    # RAM
    if m["ram_pct"] >= RAM_CRITICAL:
        if node_can_alert(uuid, "ram_crit"):
            tg_metrics(
                f"⚠️ <b>RAM критично</b>\nНода: <b>{name}</b>\n"
                f"RAM: <code>{m['ram_pct']:.0f}%</code> (порог {RAM_CRITICAL:.0f}%)"
            )
    elif node_alerts.get(uuid, {}).get("ram_crit", 0) > 0 and m["ram_pct"] < RAM_CRITICAL - 5:
        node_reset_alert(uuid, "ram_crit")
        tg_metrics(f"✅ <b>RAM восстановился</b>\nНода: <b>{name}</b>\nRAM: <code>{m['ram_pct']:.0f}%</code>")

    # Speedtest аномально низкий (проверяем раз в 8ч)
    if 0 < m["speedtest_mbps"] < SPEEDTEST_WARN:
        if node_can_alert(uuid, "spd_low", cooldown=3600 * 8):
            tg_metrics(
                f"🐢 <b>Нода аномально медленная</b>\nНода: <b>{name}</b>\n"
                f"Speedtest upload: <code>{m['speedtest_mbps']:.1f} Mbps</code> "
                f"(порог {SPEEDTEST_WARN:.0f} Mbps)\nПроверь тариф VPS или сетевой интерфейс"
            )

    # Юзеров слишком много
    if active_users is not None and active_users >= MAX_USERS:
        if node_can_alert(uuid, "users_full"):
            tg_metrics(
                f"👥 <b>Нода переполнена</b>\nНода: <b>{name}</b>\n"
                f"Активных пользователей: <code>{active_users}</code> (макс {MAX_USERS})"
            )

    # ── Логика пула ─────────────────────────────────────────────
    if score > SCORE_BAD and in_pool:
        if len(nodes_in_pool) <= 1:
            log.warning(f"{name}: перегружена, но единственная — оставляем")
            tg_critical(
                f"⚠️ <b>Деградация — последняя нода перегружена</b>\n"
                f"Нода: <b>{name}</b>  score=<code>{score}</code>\n"
                f"Пользователи ещё подключены, но качество снижено.\n"
                f"<code>{detail}</code>"
            )
            return
        if set_host_tag(uuid, ""):
            node_state[uuid] = False
            log.warning(f"{name}: ВЫВЕДЕНА из пула [{node_tag}] | score={score}")
            tg_metrics(
                f"🔴 <b>Нода выведена из пула</b>\nНода: <b>{name}</b>\n"
                f"Score: <code>{score}</code> (порог {SCORE_BAD})\n<code>{detail}</code>"
            )
            same_pool = [n["host_uuid"] for n in NODES if n.get("pool_tag", BALANCER_TAG) == node_tag]
            if not any(node_state.get(u, False) for u in same_pool):
                tg_critical(
                    f"🚨 <b>СЕРВИС НЕДОСТУПЕН — все ноды упали</b>\n"
                    f"Пул: <code>{node_tag}</code>  подключения невозможны.\n"
                    f"Последней выведена: <b>{name}</b>  score=<code>{score}</code>\n"
                    f"<code>{detail}</code>"
                )

    elif score < SCORE_GOOD and not in_pool:
        if set_host_tag(uuid, node_tag):
            node_state[uuid] = True
            log.info(f"{name}: ВОЗВРАЩЕНА в пул | score={score}")
            tg_metrics(
                f"🟢 <b>Нода возвращена в пул</b>\nНода: <b>{name}</b>\n"
                f"Score: <code>{score}</code> (порог {SCORE_GOOD})\n<code>{detail}</code>"
            )

# ── Синхронизация состояния ─────────────────────────────────────

def sync_state():
    try:
        hosts = _fetch_hosts()
        if not hosts:
            log.warning("sync_state: пустой список хостов от API")
            return
        for node in NODES:
            host = next((h for h in hosts if h["uuid"] == node["host_uuid"]), None)
            if host:
                node_tag = node.get("pool_tag", BALANCER_TAG)
                in_pool = host.get("tag") == node_tag
                node_state[node["host_uuid"]] = in_pool
                log.info(f"{node['name']}: {'в пуле' if in_pool else 'вне пула'}")
            else:
                log.warning(f"{node['name']}: не найден в Remnawave (uuid={node['host_uuid']})")
    except Exception as e:
        log.error(f"sync_state error: {e}")

# ── Main ───────────────────────────────────────────────────────

def main():
    global last_digest_day
    log.info("=== VPN Balancer запущен ===")
    tg_metrics("🚀 <b>VPN Balancer запущен</b>\nМониторинг нод активен.")
    sync_state()
    while True:
        try:
            now = datetime.datetime.utcnow()
            if now.hour == DIGEST_HOUR and now.day != last_digest_day:
                send_daily_digest()
                send_daily_log()
                last_digest_day = now.day
            for node in NODES:
                node_tag = node.get("pool_tag", BALANCER_TAG)
                pool_uuids = [n["host_uuid"] for n in NODES if n.get("pool_tag", BALANCER_TAG) == node_tag]
                active_in_pool = [u for u in pool_uuids if node_state.get(u, True)]
                check_node(node, active_in_pool)
        except Exception as e:
            log.error(f"Ошибка главного цикла: {e}")
        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    main()
