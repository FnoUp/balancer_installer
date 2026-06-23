#!/usr/bin/env python3
import requests, time, logging, sys, datetime
from pathlib import Path

PROMETHEUS_URL      = "http://localhost:9090"
REMNAWAVE_API       = "https://%%DOMAIN%%/api"
REMNAWAVE_TOKEN     = "%%RW_TOKEN%%"
REMNAWAVE_COOKIE    = "%%RW_COOKIE%%"

TG_BOT_TOKEN         = "%%TG_TOKEN%%"

# Отдельный чат балансировщика — все штатные алерты
TG_METRICS_CHAT_ID   = "%%TG_METRICS_CHAT%%"
TG_METRICS_TOPIC_ID  = %%TG_METRICS_TOPIC%%

# Чат варнингов бота — критично: все ноды упали / осталась 1
TG_ERRORS_CHAT_ID    = "%%TG_ERRORS_CHAT%%"
TG_ERRORS_TOPIC_ID   = %%TG_ERRORS_TOPIC%%

# Чат отчётов бота — ежедневный дайджест score
TG_REPORTS_CHAT_ID   = "%%TG_REP_CHAT%%"
TG_REPORTS_TOPIC_ID  = %%TG_REP_TOPIC%%

BALANCER_NAME    = "%%BALANCER_NAME%%"
BALANCER_TAG     = "%%BALANCER_TAG%%"
LOG_FILE         = "/var/log/%%SVC_NAME%%/balancer.log"
CHECK_INTERVAL   = 120
DIGEST_HOUR      = 9       # час UTC для ежедневного дайджеста
ALERT_COOLDOWN   = 1800    # не спамим одну ошибку чаще раза в 30 мин

SCORE_BAD        = 0.75
SCORE_GOOD       = 0.55
CPU_CRITICAL     = 90.0
RAM_CRITICAL     = 90.0
MAX_PING_MS      = 300.0
MAX_CONNECTIONS  = 1000.0

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

node_state           = {}
last_prom_err_alert  = 0
last_api_err_alert   = 0
last_digest_day      = -1

# ── Telegram ───────────────────────────────────────────────────────────────────

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
        log.warning(f"Telegram send failed: {e}")

# Штатные алерты балансировщика → отдельный чат метрик
def tg_metrics(text):
    tg_send(TG_METRICS_CHAT_ID, TG_METRICS_TOPIC_ID, text)

# Критические алерты → чат варнингов бота
def tg_critical(text):
    tg_send(TG_ERRORS_CHAT_ID, TG_ERRORS_TOPIC_ID, text)

# Ежедневный дайджест → чат отчётов бота
def tg_report(text):
    tg_send(TG_REPORTS_CHAT_ID, TG_REPORTS_TOPIC_ID, text)

# ── Prometheus ─────────────────────────────────────────────────────────────────

def prom_query(q):
    global last_prom_err_alert
    try:
        r = requests.get(f"{PROMETHEUS_URL}/api/v1/query", params={"query": q}, timeout=10)
        results = r.json()["data"]["result"]
        return float(results[0]["value"][1]) if results else None
    except Exception as e:
        log.warning(f"Prometheus query failed [{q[:60]}]: {e}")
        now = time.time()
        if now - last_prom_err_alert > ALERT_COOLDOWN:
            last_prom_err_alert = now
            tg_metrics(f"❌ <b>Prometheus недоступен</b>\nМетрики не читаются\n<code>{e}</code>")
        return None

# ── Remnawave API ──────────────────────────────────────────────────────────────

def set_host_tag(host_uuid, tag):
    global last_api_err_alert
    try:
        r = requests.patch(
            f"{REMNAWAVE_API}/hosts",
            headers={
                "Authorization":  f"Bearer {REMNAWAVE_TOKEN}",
                "Cookie":         REMNAWAVE_COOKIE,
                "Content-Type":   "application/json",
            },
            json={"uuid": host_uuid, "tag": tag},
            timeout=15,
        )
        if r.status_code != 200:
            raise Exception(f"HTTP {r.status_code}: {r.text[:100]}")
        return True
    except Exception as e:
        log.error(f"Remnawave API error: {e}")
        now = time.time()
        if now - last_api_err_alert > ALERT_COOLDOWN:
            last_api_err_alert = now
            tg_metrics(f"❌ <b>Ошибка API Remnawave</b>\nНе удалось обновить тег хоста\n<code>{e}</code>")
        return False

# ── Метрики ────────────────────────────────────────────────────────────────────

def clamp(val, lo=0.0, hi=1.0):
    return max(lo, min(hi, val))

def get_metrics(node):
    inst = node["prom_instance"]
    ping = node["ping_instance"]
    dev  = node["net_device"]
    ping_ms     = prom_query(f"probe_duration_seconds{{job='vpn_ping',instance='{ping}'}} * 1000")
    ping_ok     = prom_query(f"probe_success{{job='vpn_ping',instance='{ping}'}}")
    cpu_pct     = prom_query(f"100-(avg(rate(node_cpu_seconds_total{{instance='{inst}',mode='idle'}}[5m]))*100)")
    ram_pct     = prom_query(f"100*(1-node_memory_MemAvailable_bytes{{instance='{inst}'}}/node_memory_MemTotal_bytes{{instance='{inst}'}})")
    tx_mbps     = prom_query(f"rate(node_network_transmit_bytes_total{{instance='{inst}',device='{dev}'}}[5m])*8/1024/1024")
    tx_max_mbps = prom_query(f"max_over_time(rate(node_network_transmit_bytes_total{{instance='{inst}',device='{dev}'}}[5m])[24h:5m])*8/1024/1024")
    tcp_conn    = prom_query(f"node_netstat_Tcp_CurrEstab{{instance='{inst}'}}")
    if None in (ping_ms, cpu_pct, ram_pct, tx_mbps):
        return None
    return {
        "ping_ms":     ping_ms,
        "ping_ok":     ping_ok if ping_ok is not None else 1.0,
        "cpu_pct":     cpu_pct,
        "ram_pct":     ram_pct,
        "tx_mbps":     tx_mbps,
        "tx_max_mbps": tx_max_mbps if tx_max_mbps and tx_max_mbps > 0 else max(tx_mbps, 1.0),
        "tcp_conn":    tcp_conn if tcp_conn is not None else 0.0,
    }

def calc_score(m):
    if m["ping_ok"] < 1:
        return 1.0, "ping FAIL"
    if m["cpu_pct"] >= CPU_CRITICAL:
        return 1.0, f"CPU CRITICAL {m['cpu_pct']:.0f}%"
    if m["ram_pct"] >= RAM_CRITICAL:
        return 1.0, f"RAM CRITICAL {m['ram_pct']:.0f}%"
    score = (
        clamp(m["ping_ms"] / MAX_PING_MS)                * 0.35 +
        clamp(m["tx_mbps"] / max(m["tx_max_mbps"], 1.0)) * 0.40 +
        clamp(m["tcp_conn"] / MAX_CONNECTIONS)            * 0.15 +
        clamp(m["cpu_pct"] / 100.0)                      * 0.07 +
        clamp(m["ram_pct"] / 100.0)                      * 0.03
    )
    detail = (f"ping={m['ping_ms']:.0f}ms bw={m['tx_mbps']:.1f}/{m['tx_max_mbps']:.1f}Mbps "
              f"conn={m['tcp_conn']:.0f} cpu={m['cpu_pct']:.1f}% ram={m['ram_pct']:.1f}%")
    return round(score, 4), detail

# ── Дайджест ───────────────────────────────────────────────────────────────────

def send_daily_digest():
    date_str = datetime.datetime.utcnow().strftime("%d.%m.%Y %H:%M UTC")
    lines = [f"📊 <b>Дайджест нод — {date_str}</b>\n"]
    for node in NODES:
        name    = node["name"]
        uuid    = node["host_uuid"]
        in_pool = node_state.get(uuid, False)
        status  = "● в пуле" if in_pool else "○ вне пула"
        m = get_metrics(node)
        if m is None:
            lines.append(f"<b>{name}</b>  {status}\n  ⚠️ метрики недоступны\n")
            continue
        score, _ = calc_score(m)
        icon = "🟢" if score < SCORE_GOOD else ("🟡" if score < SCORE_BAD else "🔴")
        lines.append(
            f"{icon} <b>{name}</b>  {status}\n"
            f"  score={score}  ping={m['ping_ms']:.0f}ms  "
            f"bw={m['tx_mbps']:.1f}/{m['tx_max_mbps']:.1f}Mbps  "
            f"cpu={m['cpu_pct']:.1f}%  ram={m['ram_pct']:.1f}%\n"
        )
    tg_report("\n".join(lines))
    log.info("Дайджест отправлен")

# ── Проверка ноды ──────────────────────────────────────────────────────────────

def check_node(node, nodes_in_pool):
    name    = node["name"]
    uuid    = node["host_uuid"]
    in_pool = node_state.get(uuid, True)
    metrics = get_metrics(node)
    if metrics is None:
        log.error(f"{name}: не удалось получить метрики")
        return
    score, detail = calc_score(metrics)
    log.info(f"{name}: score={score} | {detail}")
    if score > SCORE_BAD and in_pool:
        if len(nodes_in_pool) <= 1:
            log.warning(f"{name}: перегружена, но единственная — оставляем")
            tg_critical(
                f"⚠️ <b>VPN Balancer — критично</b>\n"
                f"Нода <b>{name}</b> перегружена, но единственная в пуле — не выводим\n"
                f"Score: <code>{score}</code>\n<code>{detail}</code>"
            )
            return
        if set_host_tag(uuid, ""):
            node_state[uuid] = False
            log.warning(f"{name}: ВЫВЕДЕНА из пула | score={score}")
            tg_metrics(f"🔴 <b>VPN Balancer — нода выведена</b>\nНода: <b>{name}</b>\nScore: <code>{score}</code> (порог {SCORE_BAD})\n<code>{detail}</code>")
            active_after = [u for u, s in node_state.items() if s]
            if len(active_after) == 0:
                tg_critical(f"🚨 <b>ВСЕ НОДЫ ВЫВЕДЕНЫ ИЗ ПУЛА</b>\nПользователи не могут подключиться!\nПоследняя выведена: <b>{name}</b>")
    elif score < SCORE_GOOD and not in_pool:
        if set_host_tag(uuid, BALANCER_TAG):
            node_state[uuid] = True
            log.info(f"{name}: ВОЗВРАЩЕНА в пул | score={score}")
            tg_metrics(f"🟢 <b>VPN Balancer — нода возвращена</b>\nНода: <b>{name}</b>\nScore: <code>{score}</code> (порог {SCORE_GOOD})\n<code>{detail}</code>")

# ── Синхронизация начального состояния ─────────────────────────────────────────

def sync_state():
    try:
        r = requests.get(
            f"{REMNAWAVE_API}/hosts",
            headers={"Authorization": f"Bearer {REMNAWAVE_TOKEN}", "Cookie": REMNAWAVE_COOKIE},
            timeout=15,
        )
        hosts = r.json().get("response", [])
        for node in NODES:
            host = next((h for h in hosts if h["uuid"] == node["host_uuid"]), None)
            if host:
                in_pool = host.get("tag") == BALANCER_TAG
                node_state[node["host_uuid"]] = in_pool
                log.info(f"{node['name']}: {'в пуле' if in_pool else 'вне пула'}")
    except Exception as e:
        log.error(f"sync_state error: {e}")

# ── Main ───────────────────────────────────────────────────────────────────────

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
                last_digest_day = now.day
            active = [uuid for uuid, in_pool in node_state.items() if in_pool]
            for node in NODES:
                check_node(node, active)
        except Exception as e:
            log.error(f"Ошибка главного цикла: {e}")
        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    main()
