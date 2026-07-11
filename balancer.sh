#!/bin/bash
# ============================================================
#  balancer — команда управления VPN балансировщиком
#  Установка: запускается автоматически из setup.sh
#  Использование: balancer
# ============================================================

BASE_URL="https://raw.githubusercontent.com/FnoUp/balancer_installer/master"
ADD_NODE_PY="/tmp/add_node.py"
AUTO_ADD_NODE_PY="/tmp/auto_add_node.py"
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
# ОБНОВЛЕНИЕ / УСТАНОВКА (общее для панели и ноды)
# ══════════════════════════════════════════════════════════════
update_panel_scripts() {
    echo -e "  ${BLUE}[INFO]${NC} Обновляем вспомогательные скрипты..."
    curl -4 -Ls "$BASE_URL/balancer.sh" -o /usr/local/bin/balancer && chmod +x /usr/local/bin/balancer \
        && echo -e "  ${GREEN}[OK]${NC} balancer обновлён" || echo -e "  ${YELLOW}[WARN]${NC} не удалось обновить balancer"
    curl -4 -Ls "$BASE_URL/add_node.py" -o "$ADD_NODE_PY" \
        && echo -e "  ${GREEN}[OK]${NC} add_node.py обновлён"
    curl -4 -Ls "$BASE_URL/auto_add_node.py" -o "$AUTO_ADD_NODE_PY" \
        && echo -e "  ${GREEN}[OK]${NC} auto_add_node.py обновлён"

    if [ ! -f "$BALANCER_PY" ]; then
        echo ""
        echo -e "  ${YELLOW}balancer.py не найден — запусти полную установку:${NC}"
        echo -e "  bash <(curl -4 -Ls \"$BASE_URL/setup.sh\")"
        return
    fi

    echo ""
    echo -e "  ${BLUE}[INFO]${NC} Обновляем логику balancer.py (токены и ноды сохраняются)..."

    TEMPLATE=$(curl -4 -Ls "$BASE_URL/balancer_template.py") || {
        echo -e "  ${RED}[ERROR]${NC} Не удалось скачать template"
        return
    }

    # Разделитель: конфиг (токены/ноды) — выше, логика (функции) — ниже
    SPLIT="Path(LOG_FILE).parent.mkdir"

    # Конфиг-часть из текущего файла (до маркера, не включая его)
    CONFIG=$(sed -n "1,/$SPLIT/p" "$BALANCER_PY" | head -n -1)

    # Логика из нового template (от маркера до конца)
    LOGIC=$(echo "$TEMPLATE" | sed -n "/$SPLIT/,\$p")

    if [ -z "$CONFIG" ] || [ -z "$LOGIC" ]; then
        echo -e "  ${RED}[ERROR]${NC} Не нашёл маркер разделения в файле — обновление отменено"
        return
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
}

update_node_scripts() {
    curl -4 -Ls "$BASE_URL/balancer.sh" -o /usr/local/bin/balancer && chmod +x /usr/local/bin/balancer \
        && echo -e "  ${GREEN}[OK]${NC} balancer обновлён" \
        || echo -e "  ${RED}[ERROR]${NC} Не удалось обновить"
}

update_script_menu() {
    echo -e "  ${BOLD}Что обновить?${NC}"
    echo ""
    echo -e "  ${BLUE}1)${NC} Панель"
    echo -e "  ${BLUE}2)${NC} Нода"
    echo ""
    read -rp "  Выбор: " ROLE_CHOICE
    echo ""
    case "$ROLE_CHOICE" in
        1) update_panel_scripts ;;
        2) update_node_scripts ;;
        *) echo "  Отмена." ;;
    esac
}

reinstall_panel_from_scratch() {
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
        rm -rf "/opt/$BALANCER_SVC" "/var/log/$BALANCER_SVC" /tmp/add_node.py /tmp/auto_add_node.py /etc/vpn-balancer
        rm -f "/etc/systemd/system/$BALANCER_SVC.service" /usr/local/bin/balancer
        rm -f "$TARGETS_DIR"/vpn_nodes.yml "$TARGETS_DIR"/docker.yml "$TARGETS_DIR"/ping.yml
        systemctl daemon-reload
        echo -e "  ${GREEN}[OK]${NC} Файлы удалены"
        echo ""
        exec bash <(curl -4 -Ls "$BASE_URL/setup.sh")
    else
        echo "  Отмена."
    fi
}

install_update_menu() {
    echo -e "  ${BOLD}Установка / обновление:${NC}"
    echo ""
    echo -e "  ${BLUE}1)${NC} Установить / обновить балансировщик"
    echo -e "  ${BLUE}2)${NC} Переустановить с нуля (сброс)"
    echo -e "  ${BLUE}3)${NC} Обновить скрипт (панель/нода)"
    echo ""
    read -rp "  Выбор: " SUB
    echo ""
    case "$SUB" in
        1) update_panel_scripts ;;
        2) reinstall_panel_from_scratch ;;
        3) update_script_menu ;;
        *) echo "  Отмена." ;;
    esac
}

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
    echo -e "  ${BLUE}7)${NC} Обновить скрипт (панель/нода)"
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
        update_node_scripts
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
    # ── 7. Обновить скрипт (панель/нода) ────────────────────────
    7)
        update_script_menu
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
    echo -e "  ${BLUE}1)${NC} Установка / обновление"
    echo -e "       ${DIM}(установить, обновить, переустановить с нуля, обновить скрипт)${NC}"
    echo ""
    echo -e "  ${DIM}── Ноды ──────────────────────────────────${NC}"
    echo -e "  ${BLUE}2)${NC} Добавить новую ноду"
    echo -e "  ${BLUE}3)${NC} Автодобавление ноды (полная автоматизация)"
    echo -e "       ${DIM}(добавить/удалить ноду, аудит осиротевших объектов)${NC}"
    echo -e "  ${BLUE}4)${NC} Исправить/удалить ноду"
    echo -e "       ${DIM}(если ошибся при вводе — удалит и даст ввести заново)${NC}"
    echo -e "  ${BLUE}5)${NC} Статус нод и score"
    echo ""
    echo -e "  ${DIM}── Управление ────────────────────────────${NC}"
    echo -e "  ${BLUE}6)${NC} Логи балансировщика (live)"
    echo -e "  ${BLUE}7)${NC} Перезапустить сервисы"
    echo -e "       ${DIM}(балансировщик / Prometheus / blackbox-exporter)${NC}"
    echo ""
    echo -e "  ${DIM}0) Выйти${NC}"
    echo ""
    read -rp "  Выбор: " choice
    echo ""
    handle "$choice"
}

handle() {
    case "$1" in

    # ── 1. Установка / обновление ─────────────────────────────────
    1)
        install_update_menu
        pause; show_menu
        ;;

    # ── 2. Добавить ноду ────────────────────────────────────────
    2)
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

    # ── 3. Автодобавление ноды (полная автоматизация) ───────────
    3)
        curl -4 -Ls "$BASE_URL/add_node.py" -o "$ADD_NODE_PY" && [ -s "$ADD_NODE_PY" ] \
            || { echo -e "  ${RED}[ERROR]${NC} Не удалось скачать add_node.py"; pause; show_menu; return; }
        curl -4 -Ls "$BASE_URL/auto_add_node.py" -o "$AUTO_ADD_NODE_PY" && [ -s "$AUTO_ADD_NODE_PY" ] \
            || { echo -e "  ${RED}[ERROR]${NC} Не удалось скачать auto_add_node.py"; pause; show_menu; return; }
        echo ""
        echo -e "  ${BOLD}Автодобавление:${NC}"
        echo ""
        echo -e "  ${BLUE}1)${NC} Добавить ноду(-ы)  ${DIM}(можно несколько подряд за один запуск)${NC}"
        echo -e "  ${BLUE}2)${NC} Удалить ноду полностью  ${DIM}(хост + скрытый хост + шаблон + balancer.py + Prometheus)${NC}"
        echo -e "  ${BLUE}3)${NC} Аудит  ${DIM}(найти осиротевшие хосты/шаблоны в Remnawave)${NC}"
        echo ""
        read -rp "  Выбор: " AUTO_SUB
        echo ""
        case "$AUTO_SUB" in
            1) python3 "$AUTO_ADD_NODE_PY" ;;
            2) python3 "$AUTO_ADD_NODE_PY" --remove ;;
            3) python3 "$AUTO_ADD_NODE_PY" --audit ;;
            *) echo "  Отмена." ;;
        esac
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
    content = open(BALANCER_PY, encoding="utf-8").read()
except FileNotFoundError:
    print("  balancer.py не найден"); sys.exit(0)
blocks = re.findall(r'\{[^{}]*\}', content, re.DOTALL)
nodes = [b for b in blocks if '"prom_instance"' in b]
if not nodes:
    print("  Ноды не найдены — список NODES пуст")
    sys.exit(0)
def field(b, k):
    m = re.search(r'"' + k + r'"\s*:\s*"([^"]*)"', b)
    return m.group(1) if m else "?"
for i, b in enumerate(nodes, 1):
    ip = field(b, "prom_instance").split(":")[0]
    print(f"    {i}) {field(b,'name'):<18} IP={ip:<16} пул={field(b,'pool_tag')}")
PYEOF
        echo ""
        read -rp "  Номер ноды (Enter = отмена): " NODE_NUM
        if [ -z "$NODE_NUM" ]; then
            show_menu; return
        fi

        NODE_INFO=$(python3 - "$BALANCER_PY" "$TARGETS_DIR" "$NODE_NUM" << 'PYEOF'
import re, sys, os
BALANCER_PY, TARGETS_DIR, idx = sys.argv[1], sys.argv[2], int(sys.argv[3])
content = open(BALANCER_PY, encoding="utf-8").read()
blocks = re.findall(r'\{[^{}]*\}', content, re.DOTALL)
nodes = [b for b in blocks if '"prom_instance"' in b]
if not (1 <= idx <= len(nodes)):
    print("ERROR")
    sys.exit(0)
b = nodes[idx - 1]
def field(k):
    m = re.search(r'"' + k + r'"\s*:\s*"([^"]*)"', b)
    return m.group(1) if m else ""
ip = field("prom_instance").split(":")[0]
loc = ""
vpn_yml = os.path.join(TARGETS_DIR, "vpn_nodes.yml")
if ip and os.path.exists(vpn_yml):
    fc = open(vpn_yml, encoding="utf-8").read()
    for blk in re.split(r'(?=^- targets:)', fc, flags=re.MULTILINE):
        if ip in blk:
            mloc = re.search(r'location:\s*"([^"]*)"', blk)
            loc = mloc.group(1) if mloc else ""
            break
print("\t".join([field("name"), ip, loc, field("net_device"), field("host_uuid"), field("pool_tag")]))
PYEOF
        )
        if [ "$NODE_INFO" = "ERROR" ] || [ -z "$NODE_INFO" ]; then
            echo -e "  ${RED}[ERROR]${NC} Неверный номер"
            pause; show_menu; return
        fi
        IFS=$'\t' read -r CUR_NAME CUR_IP CUR_LOC CUR_IFACE CUR_UUID CUR_POOL <<< "$NODE_INFO"

        echo ""
        echo -e "  ${BOLD}Текущие настройки — $CUR_NAME:${NC}"
        echo -e "    1) Имя:        $CUR_NAME"
        echo -e "    2) IP:         $CUR_IP"
        echo -e "    3) Локация:    $CUR_LOC"
        echo -e "    4) Интерфейс:  $CUR_IFACE"
        echo -e "    5) UUID:       $CUR_UUID"
        echo -e "    6) Пул:        $CUR_POOL"
        echo -e "    7) ${YELLOW}Всё сразу${NC} (удалить ноду и ввести заново)"
        echo -e "    0) Отмена"
        echo ""
        read -rp "  Что изменить? " FIELD_CHOICE

        case "$FIELD_CHOICE" in
        1)
            read -rp "  Новое имя (Enter = $CUR_NAME): " NEW_VAL
            NEW_VAL="${NEW_VAL:-$CUR_NAME}"
            FIELD="name"
            ;;
        2)
            read -rp "  Новый IP (Enter = $CUR_IP): " NEW_VAL
            NEW_VAL="${NEW_VAL:-$CUR_IP}"
            FIELD="ip"
            ;;
        3)
            read -rp "  Новая локация (Enter = $CUR_LOC): " NEW_VAL
            NEW_VAL="${NEW_VAL:-$CUR_LOC}"
            FIELD="location"
            ;;
        4)
            echo ""
            echo -e "    1) eth0"
            echo -e "    2) ens3"
            echo -e "    3) другой  ${DIM}(текущий: $CUR_IFACE)${NC}"
            read -rp "  Выбор (Enter = оставить $CUR_IFACE): " IFACE_CHOICE
            case "$IFACE_CHOICE" in
                1) NEW_VAL="eth0" ;;
                2) NEW_VAL="ens3" ;;
                3) read -rp "  Интерфейс: " NEW_VAL; NEW_VAL="${NEW_VAL:-$CUR_IFACE}" ;;
                *) NEW_VAL="$CUR_IFACE" ;;
            esac
            FIELD="iface"
            ;;
        5)
            read -rp "  Новый UUID (Enter = $CUR_UUID): " NEW_VAL
            NEW_VAL="${NEW_VAL:-$CUR_UUID}"
            FIELD="uuid"
            ;;
        6)
            echo ""
            echo -e "    1) BALANCER"
            echo -e "    2) BALANCER_WIFI"
            echo -e "    3) BALANCER_MOBILE"
            read -rp "  Выбор (Enter = оставить $CUR_POOL): " POOL_CHOICE
            case "$POOL_CHOICE" in
                1) NEW_VAL="BALANCER" ;;
                2) NEW_VAL="BALANCER_WIFI" ;;
                3) NEW_VAL="BALANCER_MOBILE" ;;
                *) NEW_VAL="$CUR_POOL" ;;
            esac
            FIELD="pool"
            ;;
        7)
            echo -e "  ${YELLOW}Нода \"$CUR_NAME\" ($CUR_IP) будет удалена из всех конфигов${NC}"
            read -rp "  Подтвердить? (yes/n): " CONFIRM
            if [ "$CONFIRM" != "yes" ]; then
                echo "  Отмена."
                pause; show_menu; return
            fi
            python3 - "$BALANCER_PY" "$TARGETS_DIR" "$CUR_IP" << 'PYEOF'
import re, sys, os
BALANCER_PY, TARGETS_DIR, IP = sys.argv[1], sys.argv[2], sys.argv[3]

def remove_ip_from_yml(path, ip):
    if not os.path.exists(path):
        return
    content = open(path, encoding="utf-8").read()
    if ip not in content:
        return
    blocks = re.split(r'(?=^- targets:)', content, flags=re.MULTILINE)
    blocks = [b for b in blocks if ip not in b]
    open(path, "w", encoding="utf-8").write("".join(blocks))

for fname in ("vpn_nodes.yml", "docker.yml", "ping.yml"):
    remove_ip_from_yml(os.path.join(TARGETS_DIR, fname), IP)

content = open(BALANCER_PY, encoding="utf-8").read()
new_content = re.sub(
    r'\s*\{[^}]*"prom_instance":\s*"' + re.escape(IP) + r':[^}]*\},?',
    "", content, flags=re.DOTALL,
)
open(BALANCER_PY, "w", encoding="utf-8").write(new_content)
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
            return
            ;;
        *)
            echo "  Отмена."
            pause; show_menu; return
            ;;
        esac

        # ── Точечно меняем одно поле, не трогая остальные ────────────
        python3 - "$BALANCER_PY" "$TARGETS_DIR" "$FIELD" "$NODE_NUM" "$NEW_VAL" << 'PYEOF'
import re, sys, os

BALANCER_PY, TARGETS_DIR, FIELD, IDX, NEW_VAL = sys.argv[1:6]
IDX = int(IDX)

content = open(BALANCER_PY, encoding="utf-8").read()
blocks = list(re.finditer(r'\{[^{}]*\}', content, re.DOTALL))
node_blocks = [m for m in blocks if '"prom_instance"' in m.group(0)]

if not (1 <= IDX <= len(node_blocks)):
    print("ERROR: неверный номер ноды")
    sys.exit(1)

m = node_blocks[IDX - 1]
block = m.group(0)

def get_field(b, key):
    mm = re.search(r'"' + key + r'"\s*:\s*"([^"]*)"', b)
    return mm.group(1) if mm else ""

old_ip = get_field(block, "prom_instance").split(":")[0]
field_key_map = {"name": "name", "uuid": "host_uuid", "iface": "net_device", "pool": "pool_tag"}

new_block = block
if FIELD == "ip":
    new_block = re.sub(r'("prom_instance"\s*:\s*")([^":]+)(:)',
                        lambda mm: mm.group(1) + NEW_VAL + mm.group(3), new_block)
    new_block = re.sub(r'("ping_instance"\s*:\s*")([^"]+)(")',
                        lambda mm: mm.group(1) + NEW_VAL + mm.group(3), new_block)
elif FIELD == "location":
    pass
elif FIELD in field_key_map:
    key = field_key_map[FIELD]
    new_block = re.sub(r'("' + key + r'"\s*:\s*")([^"]*)(")',
                        lambda mm: mm.group(1) + NEW_VAL + mm.group(3), new_block)
else:
    print("ERROR: неизвестное поле")
    sys.exit(1)

if new_block != block:
    content = content[:m.start()] + new_block + content[m.end():]
    open(BALANCER_PY, "w", encoding="utf-8").write(content)

if FIELD in ("ip", "name", "location") and old_ip:
    for fname in ("vpn_nodes.yml", "docker.yml", "ping.yml"):
        path = os.path.join(TARGETS_DIR, fname)
        if not os.path.exists(path):
            continue
        fcontent = open(path, encoding="utf-8").read()
        yblocks = re.split(r'(?=^- targets:)', fcontent, flags=re.MULTILINE)
        changed = False
        for i, b in enumerate(yblocks):
            if old_ip not in b:
                continue
            if FIELD == "ip":
                yblocks[i] = b.replace(old_ip, NEW_VAL)
                changed = True
            elif FIELD == "name":
                yblocks[i] = re.sub(r'(name:\s*")[^"]*(")',
                                     lambda mm: mm.group(1) + NEW_VAL + mm.group(2), b)
                changed = True
            elif FIELD == "location" and "location:" in b:
                yblocks[i] = re.sub(r'(location:\s*")[^"]*(")',
                                     lambda mm: mm.group(1) + NEW_VAL + mm.group(2), b)
                changed = True
        if changed:
            open(path, "w", encoding="utf-8").write("".join(yblocks))

print("OK")
PYEOF
        PATCH_OK=$?
        if [ "$PATCH_OK" -ne 0 ]; then
            echo -e "  ${RED}[ERROR]${NC} Не удалось изменить поле"
            pause; show_menu; return
        fi

        [ "$FIELD" = "ip" ] || [ "$FIELD" = "name" ] || [ "$FIELD" = "location" ] \
            && { systemctl restart prometheus 2>/dev/null || true; }
        systemctl restart "$BALANCER_SVC" 2>/dev/null || true
        echo -e "  ${GREEN}[OK]${NC} Изменено: $FIELD = $NEW_VAL"
        pause; show_menu
        ;;

    # ── 5. Статус нод (расширенный: score, users, ping, bw, cpu, ram) ──
    5)
        python3 - "$BALANCER_PY" "$BALANCER_LOG" << 'PYEOF'
import re, sys, json, urllib.request

BALANCER_PY, BALANCER_LOG = sys.argv[1], sys.argv[2]

try:
    content = open(BALANCER_PY, encoding="utf-8").read()
except FileNotFoundError:
    print("  balancer.py не найден")
    sys.exit(0)

nodes_raw = re.findall(
    r'\{[^}]*"name"\s*:\s*"([^"]+)"[^}]*"host_uuid"\s*:\s*"([^"]+)"[^}]*"pool_tag"\s*:\s*"([^"]+)"[^}]*\}',
    content, re.DOTALL
)
if not nodes_raw:
    print("  Ноды не найдены в balancer.py (список NODES пуст)")
    sys.exit(0)

# живая привязка тегов пула — прямо из Remnawave
api = re.search(r'REMNAWAVE_API\s*=\s*"([^"]+)"', content)
tok = re.search(r'REMNAWAVE_TOKEN\s*=\s*"([^"]+)"', content)
cook = re.search(r'REMNAWAVE_COOKIE\s*=\s*"([^"]+)"', content)
hosts_by_uuid = {}
if api and tok:
    try:
        headers = {"Authorization": f"Bearer {tok.group(1)}"}
        if cook and cook.group(1):
            headers["Cookie"] = cook.group(1)
        req = urllib.request.Request(f"{api.group(1)}/hosts", headers=headers)
        with urllib.request.urlopen(req, timeout=5) as r:
            for h in json.loads(r.read()).get("response", []):
                hosts_by_uuid[h["uuid"]] = h
    except Exception as e:
        print(f"  {chr(0x26A0)} не удалось получить теги хостов: {e}")

# последняя строка score= по каждой ноде из лога балансировщика
log_by_name = {}
try:
    with open(BALANCER_LOG, encoding="utf-8", errors="replace") as f:
        for line in f:
            m = re.search(r'\[INFO\]\s+(.+?)\s+\[(\w+)\]:\s+score=([\d.]+)\s+users=(\S+)\s+\|\s+(.+)', line)
            if m:
                log_by_name[m.group(1)] = {
                    "score": m.group(3), "users": m.group(4), "detail": m.group(5).strip()
                }
except FileNotFoundError:
    pass

print()
print(f"  {'Нода':<16} {'Пул':<16} {'В пуле':<7} {'Score':<7} Метрики")
print("  " + "─" * 100)
for name, uuid, pool_tag in nodes_raw:
    host = hosts_by_uuid.get(uuid)
    if host is None:
        in_pool, pool_color = "?", "\033[1;33m"
    else:
        is_in = pool_tag in (host.get("tags") or [])
        in_pool, pool_color = ("да", "\033[0;32m") if is_in else ("нет", "\033[0;31m")

    info = log_by_name.get(name)
    score = info["score"] if info else "?"
    detail = info["detail"] if info else "нет данных в логе (жди следующий цикл, до 2 мин)"

    print(f"  {name:<16} {pool_tag:<16} {pool_color}{in_pool:<7}\033[0m {score:<7} {detail}")

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

    # ── 7. Перезапустить сервисы ──────────────────────────────────
    7)
        echo -e "  ${BOLD}Какой сервис перезапустить?${NC}"
        echo ""
        echo -e "  ${BLUE}1)${NC} Балансировщик"
        echo -e "  ${BLUE}2)${NC} Prometheus"
        echo -e "  ${BLUE}3)${NC} blackbox-exporter"
        echo ""
        read -rp "  Выбор: " SVC_CHOICE
        echo ""
        case "$SVC_CHOICE" in
            1)
                systemctl restart "$BALANCER_SVC" \
                    && echo -e "  ${GREEN}[OK]${NC} Балансировщик перезапущен" \
                    || echo -e "  ${RED}[ERROR]${NC} Не удалось перезапустить"
                ;;
            2)
                systemctl restart prometheus \
                    && echo -e "  ${GREEN}[OK]${NC} Prometheus перезапущен" \
                    || echo -e "  ${RED}[ERROR]${NC} Не удалось перезапустить"
                ;;
            3)
                docker restart blackbox-exporter 2>/dev/null \
                    && echo -e "  ${GREEN}[OK]${NC} blackbox-exporter перезапущен" \
                    || echo -e "  ${RED}[ERROR]${NC} Не удалось перезапустить (контейнер не найден?)"
                ;;
            *) echo "  Отмена." ;;
        esac
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
