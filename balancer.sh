#!/bin/bash
# ============================================================
#  balancer — команда управления VPN балансировщиком
#  Установка: запускается автоматически из setup.sh
#  Использование: balancer
# ============================================================

BASE_URL="https://raw.githubusercontent.com/FnoUp/balancer_installer/master"
ADD_NODE_PY="/tmp/add_node.py"
TARGETS_DIR="/etc/prometheus/targets"

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

# ── Определяем режим: панель или нода ─────────────────────────
CONFIG_FILE="/etc/vpn-balancer/config"
if [ -f "$CONFIG_FILE" ]; then
    # shellcheck source=/dev/null
    source "$CONFIG_FILE"
    IS_PANEL=true
else
    IS_PANEL=false
fi

BALANCER_PY="/opt/${SVC_NAME:-vpn-balancer}/balancer.py"
BALANCER_SVC="${SVC_NAME:-vpn-balancer}"
BALANCER_LOG="/var/log/${SVC_NAME:-vpn-balancer}/balancer.log"

# ══════════════════════════════════════════════════════════════
# МЕНЮ НОДЫ
# ══════════════════════════════════════════════════════════════
show_menu_node() {
    echo ""
    echo -e "  ${BOLD}╔══════════════════════════════════════╗${NC}"
    echo -e "  ${BOLD}║     VPN Balancer — нода              ║${NC}"
    echo -e "  ${BOLD}╚══════════════════════════════════════╝${NC}"
    echo ""
    echo -e "  node_exporter  $(svc_status prometheus-node-exporter)"
    echo -e "  cAdvisor       $(docker ps --filter name=cadvisor --format '●' 2>/dev/null | grep -q '●' \
        && echo -e "${GREEN}● работает${NC}" || echo -e "${RED}● остановлен${NC}")"
    echo ""
    echo -e "  ${DIM}── Установка ─────────────────────────────${NC}"
    echo -e "  ${BLUE}1)${NC} Переустановить ноду с нуля"
    echo -e "       ${DIM}(удалит: node_exporter, cAdvisor, iptables, balancer)${NC}"
    echo -e "       ${DIM}(оставит: Docker)${NC}"
    echo ""
    echo -e "  ${DIM}── Управление ────────────────────────────${NC}"
    echo -e "  ${BLUE}2)${NC} Перезапустить node_exporter"
    echo -e "       ${DIM}(сборщик метрик CPU, RAM, диска, сети)${NC}"
    echo -e "  ${BLUE}3)${NC} Перезапустить cAdvisor"
    echo -e "       ${DIM}(сборщик метрик Docker-контейнеров)${NC}"
    echo -e "  ${BLUE}4)${NC} Firewall метрик — кто видит порты 9100/8080"
    echo -e "       ${DIM}(должна быть только панель)${NC}"
    echo -e "  ${BLUE}5)${NC} Обновить команду balancer"
    echo -e "  ${BLUE}6)${NC} Запустить speedtest вручную"
    echo -e "       ${DIM}(3 теста, обновляет файл ёмкости канала)${NC}"
    echo ""
    echo -e "  ${DIM}0) Выйти${NC}"
    echo ""
    read -rp "  Выбор: " choice
    echo ""
    handle_node "$choice"
}

handle_node() {
    case "$1" in
    # ── 1. Переустановить ноду ──────────────────────────────────
    1)
        echo -e "  ${RED}Будет удалено:${NC}"
        echo -e "    node_exporter (systemd сервис)"
        echo -e "    cAdvisor (docker контейнер)"
        echo -e "    iptables правила для портов 9100/8080"
        echo -e "    /usr/local/bin/balancer"
        echo ""
        echo -e "  ${YELLOW}Docker — не трогаем${NC}"
        echo ""
        read -rp "  Подтвердить? (yes/n): " CONFIRM
        if [ "$CONFIRM" = "yes" ]; then
            systemctl stop prometheus-node-exporter 2>/dev/null || true
            systemctl disable prometheus-node-exporter 2>/dev/null || true
            apt-get remove -y prometheus-node-exporter 2>/dev/null || true
            docker rm -f cadvisor 2>/dev/null || true
            # Удаляем все правила для портов 9100/8080 (и DROP, и ACCEPT)
            while iptables -D INPUT -p tcp --dport 9100 2>/dev/null; do :; done
            while iptables -D INPUT -p tcp --dport 8080 2>/dev/null; do :; done
            iptables-save > /etc/iptables/rules.v4 2>/dev/null || true
            rm -f /usr/local/bin/balancer
            echo -e "  ${GREEN}[OK]${NC} Нода очищена"
            echo ""
            exec bash <(curl -4 -Ls "$BASE_URL/setup.sh")
        else
            echo "  Отмена."
            pause; show_menu_node
        fi
        ;;
    # ── 2. Перезапустить node_exporter ──────────────────────────
    2)
        systemctl restart prometheus-node-exporter \
            && echo -e "  ${GREEN}[OK]${NC} node_exporter перезапущен" \
            || echo -e "  ${RED}[ERROR]${NC} Не удалось перезапустить"
        sleep 1; show_menu_node
        ;;
    # ── 3. Перезапустить cAdvisor ───────────────────────────────
    3)
        docker restart cadvisor 2>/dev/null \
            && echo -e "  ${GREEN}[OK]${NC} cAdvisor перезапущен" \
            || echo -e "  ${RED}[ERROR]${NC} Не удалось перезапустить"
        sleep 1; show_menu_node
        ;;
    # ── 4. Firewall ─────────────────────────────────────────────
    4)
        echo -e "  ${BOLD}Правила firewall для портов метрик:${NC}"
        echo ""
        iptables -L INPUT -n --line-numbers | grep -E "9100|8080" \
            || echo -e "  ${YELLOW}Правил не найдено — порты открыты для всех!${NC}"
        echo ""
        pause; show_menu_node
        ;;
    # ── 5. Обновить balancer ────────────────────────────────────
    5)
        curl -4 -Ls "$BASE_URL/balancer.sh" -o /usr/local/bin/balancer && chmod +x /usr/local/bin/balancer \
            && echo -e "  ${GREEN}[OK]${NC} balancer обновлён" \
            || echo -e "  ${RED}[ERROR]${NC} Не удалось обновить"
        pause; show_menu_node
        ;;
    # ── 6. Speedtest вручную ────────────────────────────────────
    6)
        if [ ! -f /etc/vpn-balancer/speedtest.sh ]; then
            echo -e "  ${YELLOW}[WARN]${NC} /etc/vpn-balancer/speedtest.sh не найден"
            echo -e "  Переустанови ноду чтобы создать скрипт (пункт 1)"
            pause; show_menu_node; return
        fi
        echo -e "  ${BLUE}[INFO]${NC} Запускаем 3 теста speedtest (~2 мин)..."
        echo ""
        bash /etc/vpn-balancer/speedtest.sh
        echo ""
        RESULT=$(cat /etc/vpn-balancer/node_capacity 2>/dev/null)
        [ -n "$RESULT" ] \
            && echo -e "  ${GREEN}[OK]${NC} Результат: ${BOLD}${RESULT} Mbps${NC}" \
            || echo -e "  ${YELLOW}[WARN]${NC} Результат не записан — проверь логи /var/log/vpn-speedtest.log"
        pause; show_menu_node
        ;;
    0) echo ""; exit 0 ;;
    *) show_menu_node ;;
    esac
}

# ══════════════════════════════════════════════════════════════
# МЕНЮ ПАНЕЛИ
# ══════════════════════════════════════════════════════════════
show_menu() {
    echo ""
    echo -e "  ${BOLD}╔══════════════════════════════════════╗${NC}"
    echo -e "  ${BOLD}║     VPN Balancer — управление        ║${NC}"
    echo -e "  ${BOLD}╚══════════════════════════════════════╝${NC}"
    echo ""
    echo -e "  балансировщик  $(svc_status $BALANCER_SVC)"
    echo -e "  prometheus     $(svc_status prometheus)"
    echo -e "  blackbox       $(docker ps --filter name=blackbox-exporter --format '●' 2>/dev/null | grep -q '●' \
        && echo -e "${GREEN}● работает${NC}" || echo -e "${RED}● остановлен${NC}")"
    echo ""
    echo -e "  ${DIM}── Установка ─────────────────────────────${NC}"
    echo -e "  ${BLUE}1)${NC} Установить / обновить балансировщик"
    echo -e "  ${BLUE}2)${NC} Переустановить с нуля (сброс)"
    echo ""
    echo -e "  ${DIM}── Ноды ──────────────────────────────────${NC}"
    echo -e "  ${BLUE}3)${NC} Добавить новую ноду"
    echo -e "  ${BLUE}4)${NC} Исправить/удалить ноду"
    echo -e "       ${DIM}(если ошибся при вводе — удалит и даст ввести заново)${NC}"
    echo -e "  ${BLUE}5)${NC} Статус нод и score"
    echo ""
    echo -e "  ${DIM}── Управление ────────────────────────────${NC}"
    echo -e "  ${BLUE}6)${NC} Логи балансировщика (live)"
    echo -e "  ${BLUE}7)${NC} Перезапустить балансировщик"
    echo -e "  ${BLUE}8)${NC} Перезапустить Prometheus"
    echo -e "  ${BLUE}9)${NC} Перезапустить blackbox-exporter"
    echo ""
    echo -e "  ${DIM}0) Выйти${NC}"
    echo ""
    read -rp "  Выбор: " choice
    echo ""
    handle "$choice"
}

handle() {
    case "$1" in

    # ── 1. Установить / обновить ────────────────────────────────
    1)
        echo -e "  ${BLUE}[INFO]${NC} Обновляем вспомогательные скрипты..."
        curl -4 -Ls "$BASE_URL/balancer.sh" -o /usr/local/bin/balancer && chmod +x /usr/local/bin/balancer \
            && echo -e "  ${GREEN}[OK]${NC} balancer обновлён" || echo -e "  ${YELLOW}[WARN]${NC} не удалось обновить balancer"
        curl -4 -Ls "$BASE_URL/add_node.py" -o "$ADD_NODE_PY" \
            && echo -e "  ${GREEN}[OK]${NC} add_node.py обновлён"

        if [ ! -f "$BALANCER_PY" ]; then
            echo ""
            echo -e "  ${YELLOW}balancer.py не найден — запусти полную установку:${NC}"
            echo -e "  bash <(curl -4 -Ls \"$BASE_URL/setup.sh\")"
            pause; show_menu; return
        fi

        echo ""
        echo -e "  ${BLUE}[INFO]${NC} Обновляем логику balancer.py (токены и ноды сохраняются)..."

        TEMPLATE=$(curl -4 -Ls "$BASE_URL/balancer_template.py") || {
            echo -e "  ${RED}[ERROR]${NC} Не удалось скачать template"
            pause; show_menu; return
        }

        # Разделитель: конфиг (токены/ноды) — выше, логика (функции) — ниже
        SPLIT="Path(LOG_FILE).parent.mkdir"

        # Конфиг-часть из текущего файла (до маркера, не включая его)
        CONFIG=$(sed -n "1,/$SPLIT/p" "$BALANCER_PY" | head -n -1)

        # Логика из нового template (от маркера до конца)
        LOGIC=$(echo "$TEMPLATE" | sed -n "/$SPLIT/,\$p")

        if [ -z "$CONFIG" ] || [ -z "$LOGIC" ]; then
            echo -e "  ${RED}[ERROR]${NC} Не нашёл маркер разделения в файле — обновление отменено"
            pause; show_menu; return
        fi

        # Бэкап перед заменой
        BAK="${BALANCER_PY}.bak.$(date +%F-%H%M%S)"
        cp "$BALANCER_PY" "$BAK"
        echo -e "  ${DIM}Бэкап: $BAK${NC}"

        # Записываем: старый конфиг + новая логика
        { echo "$CONFIG"; echo ""; echo "$LOGIC"; } > "$BALANCER_PY"

        systemctl restart "$BALANCER_SVC" \
            && echo -e "  ${GREEN}[OK]${NC} balancer.py обновлён и перезапущен" \
            || echo -e "  ${RED}[ERROR]${NC} Обновлён, но перезапуск не удался"

        pause; show_menu
        ;;

    # ── 2. Переустановить с нуля ────────────────────────────────
    2)
        echo -e "  ${RED}Будет удалено:${NC}"
        echo -e "    /opt/$BALANCER_SVC/"
        echo -e "    /etc/systemd/system/$BALANCER_SVC.service"
        echo -e "    $TARGETS_DIR/*.yml"
        echo -e "    /var/log/$BALANCER_SVC/"
        echo -e "    /etc/vpn-balancer/config"
        echo -e "    /tmp/add_node.py"
        echo -e "    /usr/local/bin/balancer"
        echo ""
        echo -e "  ${YELLOW}Docker, Prometheus, node_exporter, iptables — не трогаем${NC}"
        echo ""
        read -rp "  Подтвердить? (yes/n): " CONFIRM
        if [ "$CONFIRM" = "yes" ]; then
            systemctl stop "$BALANCER_SVC"    2>/dev/null || true
            systemctl disable "$BALANCER_SVC" 2>/dev/null || true
            rm -rf "/opt/$BALANCER_SVC" "/var/log/$BALANCER_SVC" /tmp/add_node.py /etc/vpn-balancer
            rm -f "/etc/systemd/system/$BALANCER_SVC.service" /usr/local/bin/balancer
            rm -f "$TARGETS_DIR"/vpn_nodes.yml "$TARGETS_DIR"/docker.yml "$TARGETS_DIR"/ping.yml
            systemctl daemon-reload
            echo -e "  ${GREEN}[OK]${NC} Файлы удалены"
            echo ""
            exec bash <(curl -4 -Ls "$BASE_URL/setup.sh")
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

    # ── 4. Исправить/удалить ноду ────────────────────────────────
    4)
        echo -e "  ${BOLD}Список нод:${NC}"
        echo ""
        python3 - "$BALANCER_PY" << 'PYEOF'
import re, sys
BALANCER_PY = sys.argv[1]
try:
    content = open(BALANCER_PY).read()
except FileNotFoundError:
    print("  balancer.py не найден"); sys.exit(0)
nodes = re.findall(r'"name"\s*:\s*"([^"]+)".*?"prom_instance"\s*:\s*"([^"]+)"', content, re.DOTALL)
if not nodes:
    print("  Ноды не найдены — список NODES пуст")
    sys.exit(0)
for i, (name, prom) in enumerate(nodes, 1):
    print(f"    {i}) {name}  ({prom})")
PYEOF
        echo ""
        read -rp "  Номер ноды для исправления (Enter = отмена): " NODE_NUM
        if [ -z "$NODE_NUM" ]; then
            show_menu; return
        fi

        NODE_IP=$(python3 - "$BALANCER_PY" "$NODE_NUM" << 'PYEOF'
import re, sys
BALANCER_PY, idx = sys.argv[1], int(sys.argv[2])
content = open(BALANCER_PY).read()
nodes = re.findall(r'"name"\s*:\s*"[^"]+".*?"prom_instance"\s*:\s*"([^":]+):', content, re.DOTALL)
if 1 <= idx <= len(nodes):
    print(nodes[idx - 1])
PYEOF
        )
        if [ -z "$NODE_IP" ]; then
            echo -e "  ${RED}[ERROR]${NC} Неверный номер"
            pause; show_menu; return
        fi

        echo -e "  ${YELLOW}Нода с IP $NODE_IP будет удалена из всех конфигов${NC}"
        read -rp "  Подтвердить? (yes/n): " CONFIRM
        if [ "$CONFIRM" != "yes" ]; then
            echo "  Отмена."
            pause; show_menu; return
        fi

        python3 - "$BALANCER_PY" "$TARGETS_DIR" "$NODE_IP" << 'PYEOF'
import re, sys, os
BALANCER_PY, TARGETS_DIR, IP = sys.argv[1], sys.argv[2], sys.argv[3]

def remove_ip_from_yml(path, ip):
    if not os.path.exists(path):
        return
    content = open(path).read()
    if ip not in content:
        return
    blocks = re.split(r'(?=^- targets:)', content, flags=re.MULTILINE)
    blocks = [b for b in blocks if ip not in b]
    open(path, "w").write("".join(blocks))

for fname in ("vpn_nodes.yml", "docker.yml", "ping.yml"):
    remove_ip_from_yml(os.path.join(TARGETS_DIR, fname), IP)

content = open(BALANCER_PY).read()
new_content = re.sub(
    r'\s*\{[^}]*"prom_instance":\s*"' + re.escape(IP) + r':[^}]*\},?',
    "", content, flags=re.DOTALL,
)
open(BALANCER_PY, "w").write(new_content)
PYEOF

        systemctl restart prometheus 2>/dev/null || true
        systemctl restart "$BALANCER_SVC" 2>/dev/null || true
        echo -e "  ${GREEN}[OK]${NC} Нода удалена из конфигов"
        echo ""
        echo -e "  ${BLUE}[INFO]${NC} Введи данные заново:"
        echo ""
        [ ! -f "$ADD_NODE_PY" ] && curl -4 -Ls "$BASE_URL/add_node.py" -o "$ADD_NODE_PY"
        python3 "$ADD_NODE_PY"
        pause; show_menu
        ;;

    # ── 5. Статус нод ───────────────────────────────────────────
    5)
        python3 - "$BALANCER_PY" << 'PYEOF'
import re, sys

BALANCER_PY = sys.argv[1] if len(sys.argv) > 1 else "/opt/vpn-balancer/balancer.py"

try:
    with open(BALANCER_PY) as f:
        content = f.read()
except FileNotFoundError:
    print("  balancer.py не найден")
    sys.exit(0)

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

print(f"\n  {'Нода':<20} {'Prometheus':<25} Статус")
print("  " + "─" * 60)
for name, uuid, prom in nodes_raw:
    try:
        import urllib.request, json
        url = f"http://localhost:9090/api/v1/query?query=up{{instance='{prom}'}}"
        with urllib.request.urlopen(url, timeout=3) as r:
            results = json.loads(r.read()).get("data", {}).get("result", [])
            status = "UP" if results and results[0]["value"][1] == "1" else "DOWN"
    except:
        status = "?"
    color = "\033[0;32m" if status == "UP" else ("\033[0;31m" if status == "DOWN" else "\033[1;33m")
    print(f"  {name:<20} {prom:<25} {color}{status}\033[0m")

print()
PYEOF
        echo ""
        read -rp "  r = обновить, Enter = в меню: " SUB
        if [ "$SUB" = "r" ]; then handle 5; else show_menu; fi
        ;;

    # ── 6. Логи ─────────────────────────────────────────────────
    6)
        if [ ! -f "$BALANCER_LOG" ]; then
            echo -e "  ${YELLOW}[WARN]${NC} Лог-файл не найден"
            pause; show_menu; return
        fi
        echo -e "  ${DIM}Ctrl+C для выхода из логов${NC}"
        echo ""
        tail -f "$BALANCER_LOG"
        show_menu
        ;;

    # ── 7. Перезапустить балансировщик ──────────────────────────
    7)
        systemctl restart "$BALANCER_SVC" \
            && echo -e "  ${GREEN}[OK]${NC} Балансировщик перезапущен" \
            || echo -e "  ${RED}[ERROR]${NC} Не удалось перезапустить"
        sleep 1; show_menu
        ;;

    # ── 8. Перезапустить Prometheus ──────────────────────────────
    8)
        systemctl restart prometheus \
            && echo -e "  ${GREEN}[OK]${NC} Prometheus перезапущен" \
            || echo -e "  ${RED}[ERROR]${NC} Не удалось перезапустить"
        sleep 1; show_menu
        ;;

    # ── 9. Перезапустить blackbox-exporter ────────────────────────
    9)
        docker restart blackbox-exporter 2>/dev/null \
            && echo -e "  ${GREEN}[OK]${NC} blackbox-exporter перезапущен" \
            || echo -e "  ${RED}[ERROR]${NC} Не удалось перезапустить (контейнер не найден?)"
        sleep 1; show_menu
        ;;

    0) echo ""; exit 0 ;;
    *) show_menu ;;
    esac
}

# ── Запуск нужного меню ────────────────────────────────────────
if [ "$IS_PANEL" = true ]; then
    show_menu
else
    show_menu_node
fi
