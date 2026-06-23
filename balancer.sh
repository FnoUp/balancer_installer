#!/bin/bash
# ============================================================
#  balancer — команда управления VPN балансировщиком
#  Установка: запускается автоматически из setup.sh
#  Использование: balancer
# ============================================================

BASE_URL="https://raw.githubusercontent.com/FnoUp/balancer_installer/master"
ADD_NODE_PY="/tmp/add_node.py"
TARGETS_DIR="/etc/prometheus/targets"

# Читаем конфиг, созданный setup.sh
CONFIG_FILE="/etc/vpn-balancer/config"
if [ -f "$CONFIG_FILE" ]; then
    # shellcheck source=/dev/null
    source "$CONFIG_FILE"
else
    SVC_NAME="vpn-balancer"
fi

BALANCER_PY="/opt/$SVC_NAME/balancer.py"
BALANCER_SVC="$SVC_NAME"
BALANCER_LOG="/var/log/$SVC_NAME/balancer.log"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; DIM='\033[2m'; BOLD='\033[1m'; NC='\033[0m'

if [ ! -t 0 ]; then exec < /dev/tty; fi
if [ "$EUID" -ne 0 ]; then echo -e "${RED}[ERROR]${NC} Запусти от root"; exit 1; fi

svc_status() {
    systemctl is-active --quiet "$1" 2>/dev/null \
        && echo -e "${GREEN}● работает${NC}" \
        || echo -e "${RED}● остановлен${NC}"
}

pause() { read -rp "  Нажми Enter чтобы вернуться в меню..."; }

# ══════════════════════════════════════════════════════════════
show_menu() {
    clear
    echo ""
    echo -e "  ${BOLD}╔══════════════════════════════════════╗${NC}"
    echo -e "  ${BOLD}║     VPN Balancer — управление        ║${NC}"
    echo -e "  ${BOLD}╚══════════════════════════════════════╝${NC}"
    echo ""
    echo -e "  балансировщик  $(svc_status $BALANCER_SVC)"
    echo -e "  prometheus     $(svc_status prometheus)"
    echo ""
    echo -e "  ${DIM}── Установка ─────────────────────────────${NC}"
    echo -e "  ${BLUE}1)${NC} Установить / обновить балансировщик"
    echo -e "  ${BLUE}2)${NC} Переустановить с нуля (сброс)"
    echo ""
    echo -e "  ${DIM}── Ноды ──────────────────────────────────${NC}"
    echo -e "  ${BLUE}3)${NC} Добавить новую ноду"
    echo -e "  ${BLUE}4)${NC} Статус нод и score"
    echo ""
    echo -e "  ${DIM}── Управление ────────────────────────────${NC}"
    echo -e "  ${BLUE}5)${NC} Логи балансировщика (live)"
    echo -e "  ${BLUE}6)${NC} Перезапустить балансировщик"
    echo -e "  ${BLUE}7)${NC} Перезапустить Prometheus"
    echo ""
    echo -e "  ${DIM}0) Выйти${NC}"
    echo ""
    read -rp "  Выбор: " choice
    echo ""
    handle "$choice"
}

# ══════════════════════════════════════════════════════════════
handle() {
    case "$1" in

    # ── 1. Установить / обновить ────────────────────────────────
    1)
        echo -e "  ${BLUE}[INFO]${NC} Обновляем скрипты..."
        curl -4 -Ls "$BASE_URL/balancer.sh" -o /usr/local/bin/balancer && chmod +x /usr/local/bin/balancer \
            && echo -e "  ${GREEN}[OK]${NC} balancer обновлён" || echo -e "  ${YELLOW}[WARN]${NC} не удалось обновить balancer"
        curl -4 -Ls "$BASE_URL/add_node.py" -o "$ADD_NODE_PY" \
            && echo -e "  ${GREEN}[OK]${NC} add_node.py обновлён"
        if [ ! -f "$BALANCER_PY" ]; then
            echo ""
            echo -e "  ${YELLOW}balancer.py не найден — запусти полную установку:${NC}"
            echo -e "  bash <(curl -4 -Ls \"$BASE_URL/setup.sh\")"
        fi
        pause; show_menu
        ;;

    # ── 2. Переустановить с нуля ────────────────────────────────
    2)
        echo -e "  ${RED}Будет удалено:${NC}"
        echo -e "    /opt/$SVC_NAME/"
        echo -e "    /etc/systemd/system/$SVC_NAME.service"
        echo -e "    $TARGETS_DIR/*.yml"
        echo -e "    /var/log/$SVC_NAME/"
        echo -e "    /etc/vpn-balancer/config"
        echo -e "    /tmp/add_node.py"
        echo -e "    /usr/local/bin/balancer"
        echo ""
        echo -e "  ${YELLOW}Docker, Prometheus, node_exporter, iptables — не трогаем${NC}"
        echo ""
        read -rp "  Подтвердить? (yes/n): " CONFIRM
        if [ "$CONFIRM" = "yes" ]; then
            systemctl stop "$BALANCER_SVC"  2>/dev/null || true
            systemctl disable "$BALANCER_SVC" 2>/dev/null || true
            rm -rf "/opt/$SVC_NAME" "/var/log/$SVC_NAME" /tmp/add_node.py /etc/vpn-balancer
            rm -f "/etc/systemd/system/$SVC_NAME.service" /usr/local/bin/balancer
            rm -f "$TARGETS_DIR"/vpn_nodes.yml "$TARGETS_DIR"/docker.yml "$TARGETS_DIR"/ping.yml
            systemctl daemon-reload
            echo -e "  ${GREEN}[OK]${NC} Файлы удалены"
            echo ""
            echo -e "  Запусти заново: bash <(curl -4 -Ls \"$BASE_URL/setup.sh\")"
            exit 0
        else
            echo "  Отмена."
            pause; show_menu
        fi
        ;;

    # ── 3. Добавить ноду ────────────────────────────────────────
    3)
        echo -e "  ${BOLD}Шаг 1 — запусти на ноде:${NC}"
        echo ""
        echo -e "    bash <(curl -4 -Ls \"$BASE_URL/setup.sh\")"
        echo -e "    ${DIM}(выбери пункт 2 — установить на ноду)${NC}"
        echo ""
        read -rp "  Нода готова? Продолжить добавление на панели? (y/n): " READY
        if [ "$READY" = "y" ]; then
            [ ! -f "$ADD_NODE_PY" ] && curl -4 -Ls "$BASE_URL/add_node.py" -o "$ADD_NODE_PY"
            echo ""
            python3 "$ADD_NODE_PY"
        fi
        pause; show_menu
        ;;

    # ── 4. Статус нод ───────────────────────────────────────────
    4)
        python3 - "$BALANCER_PY" << 'PYEOF'
import re, subprocess, sys

BALANCER_PY = sys.argv[1] if len(sys.argv) > 1 else "/opt/vpn-balancer/balancer.py"

try:
    with open(BALANCER_PY) as f:
        content = f.read()
except FileNotFoundError:
    print("  balancer.py не найден")
    sys.exit(0)

# Парсим NODES из файла
nodes_raw = re.findall(
    r'\{[^}]*"name"\s*:\s*"([^"]+)"[^}]*"host_uuid"\s*:\s*"([^"]+)"[^}]*"prom_instance"\s*:\s*"([^"]+)"[^}]*\}',
    content, re.DOTALL
)
if not nodes_raw:
    nodes_raw = re.findall(
        r'"name"\s*:\s*"([^"]+)".*?"host_uuid"\s*:\s*"([^"]+)".*?"prom_instance"\s*:\s*"([^"]+)"',
        content, re.DOTALL
    )

if not nodes_raw:
    print("  Ноды не найдены в balancer.py (список NODES пуст)")
    sys.exit(0)

print(f"\n  {'Нода':<20} {'Prometheus':<25} Статус в файле")
print("  " + "─" * 60)
for name, uuid, prom in nodes_raw:
    # Проверяем доступность метрики
    try:
        import urllib.request
        url = f"http://localhost:9090/api/v1/query?query=up{{instance='{prom}'}}"
        with urllib.request.urlopen(url, timeout=3) as r:
            import json
            data = json.loads(r.read())
            results = data.get("data", {}).get("result", [])
            status = "UP" if results and results[0]["value"][1] == "1" else "DOWN"
    except:
        status = "?"
    color = "\033[0;32m" if status == "UP" else ("\033[0;31m" if status == "DOWN" else "\033[1;33m")
    print(f"  {name:<20} {prom:<25} {color}{status}\033[0m")

print()
PYEOF
        echo ""
        read -rp "  r = обновить данные, Enter = в меню: " SUB
        if [ "$SUB" = "r" ]; then handle 4; else show_menu; fi
        ;;

    # ── 5. Логи ─────────────────────────────────────────────────
    5)
        if [ ! -f "$BALANCER_LOG" ]; then
            echo -e "  ${YELLOW}[WARN]${NC} Лог-файл не найден"
            pause; show_menu; return
        fi
        echo -e "  ${DIM}Ctrl+C для выхода из логов${NC}"
        echo ""
        tail -f "$BALANCER_LOG"
        show_menu
        ;;

    # ── 6. Перезапустить балансировщик ──────────────────────────
    6)
        systemctl restart "$BALANCER_SVC" \
            && echo -e "  ${GREEN}[OK]${NC} Балансировщик перезапущен" \
            || echo -e "  ${RED}[ERROR]${NC} Не удалось перезапустить"
        sleep 1; show_menu
        ;;

    # ── 7. Перезапустить Prometheus ──────────────────────────────
    7)
        systemctl restart prometheus \
            && echo -e "  ${GREEN}[OK]${NC} Prometheus перезапущен" \
            || echo -e "  ${RED}[ERROR]${NC} Не удалось перезапустить"
        sleep 1; show_menu
        ;;

    0) echo ""; exit 0 ;;
    *) show_menu ;;
    esac
}

show_menu
