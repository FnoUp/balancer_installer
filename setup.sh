#!/bin/bash
# ============================================================
#  VPN Balancer — единый установщик
#  Запуск: bash <(curl -4 -Ls "https://raw.githubusercontent.com/FnoUp/vps_installer/main/setup.sh")
# ============================================================

BASE_URL="https://raw.githubusercontent.com/FnoUp/balancer_installer/master"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; BOLD='\033[1m'; NC='\033[0m'

info()    { echo -e "  ${BLUE}[INFO]${NC} $1"; }
success() { echo -e "  ${GREEN}[OK]${NC} $1"; }
warn()    { echo -e "  ${YELLOW}[WARN]${NC} $1"; }
error()   { echo -e "  ${RED}[ERROR]${NC} $1"; exit 1; }

if [ ! -t 0 ]; then exec < /dev/tty; fi
if [ "$EUID" -ne 0 ]; then error "Запусти от root"; fi

get_env() {
    grep -E "^${2}=" "$1" 2>/dev/null | head -1 | cut -d= -f2- | tr -d '"' | tr -d "'" | xargs
}

# ══════════════════════════════════════════════════════════════
# УСТАНОВКА НА ПАНЕЛЬ
# ══════════════════════════════════════════════════════════════
setup_panel() {
    echo ""
    echo -e "  ${BOLD}── Установка на панель ──────────────────${NC}"
    echo ""

    RW_ENV="/opt/remnawave/.env"
    BD_ENV="/opt/bedolaga-bot/.env"

    # ── Имя и тег балансировщика ───────────────────────────────
    echo -e "  ${YELLOW}Если балансировщиков будет несколько (EU + US), дай каждому уникальное имя${NC}"
    read -rp "  Имя балансировщика (Enter = vpn-balancer): " BALANCER_NAME
    BALANCER_NAME="${BALANCER_NAME:-vpn-balancer}"
    SVC_NAME=$(echo "$BALANCER_NAME" | tr ' ' '-' | tr '[:upper:]' '[:lower:]')

    read -rp "  Тег в Remnawave    (Enter = BALANCER): " BALANCER_TAG
    BALANCER_TAG="${BALANCER_TAG:-BALANCER}"
    echo ""
    success "Сервис:  $SVC_NAME"
    success "Тег:     $BALANCER_TAG"
    echo ""

    # ── Remnawave домен ────────────────────────────────────────
    if [ -f "$RW_ENV" ]; then
        DOMAIN=$(get_env "$RW_ENV" "FRONT_END_DOMAIN")
        [ -n "$DOMAIN" ] && success "Домен панели: $DOMAIN"
    fi
    if [ -z "$DOMAIN" ]; then
        read -rp "  Домен панели (например panel.example.com): " DOMAIN
    fi

    # ── Bedolaga TG настройки (авточтение) ────────────────────
    if [ -f "$BD_ENV" ]; then
        TG_TOKEN=$(get_env    "$BD_ENV" "BOT_TOKEN")
        TG_ERR_CHAT=$(get_env "$BD_ENV" "ADMIN_NOTIFICATIONS_CHAT_ID")
        TG_ERR_TOP=$(get_env  "$BD_ENV" "ADMIN_NOTIFICATIONS_ERRORS_TOPIC_ID")
        TG_REP_TOP=$(get_env  "$BD_ENV" "ADMIN_REPORTS_TOPIC_ID")
        [ -n "$TG_TOKEN"    ] && success "TG Bot Token найден автоматически"
        [ -n "$TG_ERR_CHAT" ] && success "TG Chat ID найден автоматически: $TG_ERR_CHAT"
    fi

    [ -z "$TG_TOKEN" ] && { read -rp "  TG Bot Token: " TG_TOKEN; }

    # ── Чат один общий для метрик/ошибок/отчётов, отличаются топики ──
    echo ""
    echo -e "  ${YELLOW}Один чат используется для всех уведомлений — топики внутри него разные${NC}"
    CHAT_DEFAULT="${TG_ERR_CHAT:--1003978283456}"
    read -rp "  TG Chat ID (Enter = $CHAT_DEFAULT): " TG_CHAT_INPUT
    TG_ERR_CHAT="${TG_CHAT_INPUT:-$CHAT_DEFAULT}"
    TG_REP_CHAT="$TG_ERR_CHAT"
    TG_MET_CHAT="$TG_ERR_CHAT"

    ERR_TOP_DEFAULT="${TG_ERR_TOP:-233}"
    read -rp "  Topic ID — Ошибки/критика  (Enter = $ERR_TOP_DEFAULT): " TG_ERR_TOP_INPUT
    TG_ERR_TOP="${TG_ERR_TOP_INPUT:-$ERR_TOP_DEFAULT}"

    REP_TOP_DEFAULT="${TG_REP_TOP:-6}"
    read -rp "  Topic ID — Отчёты          (Enter = $REP_TOP_DEFAULT): " TG_REP_TOP_INPUT
    TG_REP_TOP="${TG_REP_TOP_INPUT:-$REP_TOP_DEFAULT}"

    read -rp "  Topic ID — Метрики (Enter = 0, без топика): " TG_MET_TOP
    TG_MET_TOP="${TG_MET_TOP:-0}"

    # ── Remnawave API токен ────────────────────────────────────
    echo ""
    echo -e "  ${YELLOW}Создай API токен: панель → Settings → API Tokens${NC}"
    read -rp "  Remnawave API Token: " RW_TOKEN

    # ── Cookie из nginx ────────────────────────────────────────
    RW_COOKIE=""
    NGINX_CONF=$(find /opt/remnawave -name "*.conf" 2>/dev/null | head -1)
    if [ -n "$NGINX_CONF" ]; then
        COOKIE_VAL=$(grep -o 'tufLczDD=[A-Za-z0-9_.+/=-]*' "$NGINX_CONF" 2>/dev/null | head -1 | cut -d= -f2-)
        [ -n "$COOKIE_VAL" ] && RW_COOKIE="tufLczDD=$COOKIE_VAL" && success "Cookie найдена автоматически"
    fi
    if [ -z "$RW_COOKIE" ]; then
        read -rp "  Cookie (tufLczDD=..., Enter если не нужна): " RW_COOKIE
        # Снимаем случайные кавычки если пользователь скопировал с ними
        RW_COOKIE=$(echo "$RW_COOKIE" | tr -d '"' | tr -d "'")
    fi

    # ── Подтверждение ──────────────────────────────────────────
    echo ""
    echo -e "  ── Параметры ──────────────────────────────────────────"
    info "Сервис:        $SVC_NAME  |  Тег: $BALANCER_TAG"
    info "Домен:         $DOMAIN"
    info "TG Chat:       $TG_ERR_CHAT"
    info "  Метрики topic: $TG_MET_TOP  |  Ошибки topic: $TG_ERR_TOP  |  Отчёты topic: $TG_REP_TOP"
    echo "  ───────────────────────────────────────────────────────"
    echo ""
    read -rp "  Всё верно? (y = установить, n = ввести заново, q = выйти): " CONFIRM
    [[ "$CONFIRM" == "q" ]] && { echo "Выход."; exit 0; }
    [[ "$CONFIRM" != "y" ]] && { echo ""; setup_panel; return; }

    INSTALL_DIR="/opt/$SVC_NAME"
    LOG_DIR="/var/log/$SVC_NAME"

    echo ""
    info "Очищаем остатки прерванной установки..."
    systemctl stop "$SVC_NAME" 2>/dev/null || true
    systemctl disable "$SVC_NAME" 2>/dev/null || true
    rm -f "/etc/systemd/system/$SVC_NAME.service"
    rm -rf "$INSTALL_DIR"
    rm -f /tmp/add_node.py /usr/local/bin/balancer
    systemctl daemon-reload

    info "Создаём директории..."
    mkdir -p "$INSTALL_DIR" "$LOG_DIR" /etc/prometheus/targets

    info "Скачиваем balancer.py..."
    curl -4 -Ls --max-time 30 "$BASE_URL/balancer_template.py" -o "$INSTALL_DIR/balancer.py" \
        || error "Не удалось скачать balancer_template.py с GitHub"
    [ ! -s "$INSTALL_DIR/balancer.py" ] && error "Скачанный файл пустой — проверь URL в BASE_URL"

    info "Проверяем python3..."
    command -v python3 &>/dev/null || apt-get install -y python3
    python3 -c "import requests" 2>/dev/null || apt-get install -y python3-requests

    info "Заполняем конфиг..."
    # Python-подстановка безопасна для любых символов в значениях (кавычки, слэши, &)
    P_FILE="$INSTALL_DIR/balancer.py" \
    P_BALANCER_NAME="$BALANCER_NAME" P_BALANCER_TAG="$BALANCER_TAG" P_SVC_NAME="$SVC_NAME" \
    P_DOMAIN="$DOMAIN" P_RW_TOKEN="$RW_TOKEN" P_RW_COOKIE="$RW_COOKIE" P_TG_TOKEN="$TG_TOKEN" \
    P_TG_METRICS_CHAT="$TG_MET_CHAT" P_TG_METRICS_TOPIC="$TG_MET_TOP" \
    P_TG_ERRORS_CHAT="$TG_ERR_CHAT" P_TG_ERRORS_TOPIC="$TG_ERR_TOP" \
    P_TG_REP_CHAT="$TG_REP_CHAT"    P_TG_REP_TOPIC="$TG_REP_TOP" \
    python3 << 'PYEOF'
import os
f = os.environ["P_FILE"]
content = open(f).read()
for ph, env in [
    ("%%BALANCER_NAME%%",    "P_BALANCER_NAME"),
    ("%%BALANCER_TAG%%",     "P_BALANCER_TAG"),
    ("%%SVC_NAME%%",         "P_SVC_NAME"),
    ("%%DOMAIN%%",           "P_DOMAIN"),
    ("%%RW_TOKEN%%",         "P_RW_TOKEN"),
    ("%%RW_COOKIE%%",        "P_RW_COOKIE"),
    ("%%TG_TOKEN%%",         "P_TG_TOKEN"),
    ("%%TG_METRICS_CHAT%%",  "P_TG_METRICS_CHAT"),
    ("%%TG_METRICS_TOPIC%%", "P_TG_METRICS_TOPIC"),
    ("%%TG_ERRORS_CHAT%%",   "P_TG_ERRORS_CHAT"),
    ("%%TG_ERRORS_TOPIC%%",  "P_TG_ERRORS_TOPIC"),
    ("%%TG_REP_CHAT%%",      "P_TG_REP_CHAT"),
    ("%%TG_REP_TOPIC%%",     "P_TG_REP_TOPIC"),
]:
    content = content.replace(ph, os.environ.get(env, ""))
open(f, "w").write(content)
PYEOF

    info "Создаём targets файлы..."
    for f in vpn_nodes.yml docker.yml ping.yml; do
        [ ! -f "/etc/prometheus/targets/$f" ] && echo "" > "/etc/prometheus/targets/$f"
    done

    # ── Prometheus + node_exporter + blackbox-exporter ─────────────
    info "Устанавливаем Prometheus + node_exporter..."
    apt-get update -q
    apt-get install -y prometheus prometheus-node-exporter
    systemctl enable --now prometheus-node-exporter

    info "Поднимаем blackbox-exporter (Docker, для ICMP-пинга нод)..."
    if ! command -v docker &>/dev/null; then
        curl -fsSL https://get.docker.com | sh
        systemctl enable docker && systemctl start docker
    fi
    docker rm -f blackbox-exporter 2>/dev/null || true
    docker run -d --name blackbox-exporter --restart=always \
        -p 127.0.0.1:9115:9115 prom/blackbox-exporter

    info "Пишем /etc/prometheus/prometheus.yml..."
    cat > /etc/prometheus/prometheus.yml << 'PROM_EOF'
global:
  scrape_interval:     15s
  evaluation_interval: 15s

scrape_configs:
  - job_name: "vpn_ping"
    metrics_path: /probe
    params:
      module:
        - icmp
    file_sd_configs:
      - files:
          - "/etc/prometheus/targets/ping.yml"
    relabel_configs:
      - source_labels: [__address__]
        target_label: __param_target
      - source_labels: [__address__]
        target_label: instance
      - target_label: __address__
        replacement: 127.0.0.1:9115

  - job_name: "vpn_nodes"
    file_sd_configs:
      - files:
          - "/etc/prometheus/targets/vpn_nodes.yml"
          - "/etc/prometheus/targets/docker.yml"

  - job_name: 'prometheus'
    scrape_interval: 5s
    scrape_timeout: 5s
    static_configs:
      - targets: ['localhost:9090']

  - job_name: node
    static_configs:
      - targets: ['localhost:9100']
PROM_EOF

    systemctl enable prometheus
    systemctl restart prometheus
    systemctl is-active --quiet prometheus \
        && success "Prometheus запущен" \
        || warn "Prometheus не запустился — проверь journalctl -u prometheus"

    info "Создаём systemd сервис..."
    cat > "/etc/systemd/system/$SVC_NAME.service" << EOF
[Unit]
Description=VPN Balancer: $BALANCER_NAME
After=network.target prometheus.service

[Service]
Type=simple
User=root
WorkingDirectory=$INSTALL_DIR
ExecStart=/usr/bin/python3 $INSTALL_DIR/balancer.py
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

    systemctl daemon-reload
    systemctl enable "$SVC_NAME"
    systemctl start "$SVC_NAME"

    info "Сохраняем конфиг балансировщика..."
    mkdir -p /etc/vpn-balancer
    cat > /etc/vpn-balancer/config << EOF
SVC_NAME=$SVC_NAME
BALANCER_NAME=$BALANCER_NAME
BALANCER_TAG=$BALANCER_TAG
EOF

    info "Устанавливаем команду balancer..."
    curl -4 -Ls "$BASE_URL/balancer.sh" -o /usr/local/bin/balancer
    chmod +x /usr/local/bin/balancer

    echo ""
    echo -e "  ${BOLD}╔══════════════════════════════════════╗${NC}"
    echo -e "  ${BOLD}║   Панель настроена!                  ║${NC}"
    echo -e "  ${BOLD}╚══════════════════════════════════════╝${NC}"
    echo ""
    success "Сервис запущен: $SVC_NAME"
    success "Тег Remnawave:  $BALANCER_TAG"
    success "Команда:        balancer"
    echo ""
    echo -e "  Следующий шаг: добавь ноды через ${BLUE}balancer → пункт 3${NC}"
    echo ""
}

# ══════════════════════════════════════════════════════════════
# УСТАНОВКА НА НОДУ
# ══════════════════════════════════════════════════════════════
setup_node() {
    echo ""
    echo -e "  ${BOLD}── Установка на ноду ────────────────────${NC}"
    echo ""

    read -rp "  IP адрес панели (Remnawave): " PANEL_IP
    [ -z "$PANEL_IP" ] && error "IP панели не может быть пустым"

    # ── Автоопределение интерфейса ─────────────────────────────
    IFACES=()
    while IFS= read -r iface; do
        IFACES+=("$iface")
    done < <(ip -o link show | awk -F': ' '{print $2}' | grep -vE '^lo$|^docker|^veth|^br-|^virbr')

    if [ "${#IFACES[@]}" -eq 0 ]; then
        warn "Не удалось определить интерфейс, используем eth0"
        NET_DEV="eth0"
    elif [ "${#IFACES[@]}" -eq 1 ]; then
        NET_DEV="${IFACES[0]}"
        IFACE_IP=$(ip -4 addr show "$NET_DEV" 2>/dev/null | awk '/inet / {split($2,a,"/"); print a[1]; exit}')
        info "Найден интерфейс: ${YELLOW}$NET_DEV${NC} (IP: ${IFACE_IP:-нет IP})"
        read -rp "  Использовать его? (Enter = $NET_DEV, или введи другой): " NET_DEV_INPUT
        NET_DEV="${NET_DEV_INPUT:-$NET_DEV}"
    else
        echo ""
        info "Найдено несколько интерфейсов:"
        for i in "${!IFACES[@]}"; do
            IFACE="${IFACES[$i]}"
            IFACE_IP=$(ip -4 addr show "$IFACE" 2>/dev/null | awk '/inet / {split($2,a,"/"); print a[1]; exit}')
            echo "    $((i+1))) $IFACE   ${IFACE_IP:-нет IP}"
        done
        echo ""
        read -rp "  Выбери номер (Enter = 1): " IFACE_NUM
        IFACE_NUM="${IFACE_NUM:-1}"
        if [[ "$IFACE_NUM" =~ ^[0-9]+$ ]] && [ "$IFACE_NUM" -ge 1 ] && [ "$IFACE_NUM" -le "${#IFACES[@]}" ]; then
            NET_DEV="${IFACES[$((IFACE_NUM-1))]}"
        else
            NET_DEV="${IFACES[0]}"
        fi
    fi

    echo ""
    echo -e "  В какой пул войдёт нода?"
    echo ""
    echo -e "    ${BLUE}1)${NC} BALANCER        — основной трафик ${YELLOW}(по умолчанию)${NC}"
    echo -e "    ${BLUE}2)${NC} BALANCER_WIFI   — для WiFi-клиентов"
    echo -e "    ${BLUE}3)${NC} BALANCER_MOBILE — для мобильных клиентов"
    echo ""
    read -rp "  Выбор (Enter = 1): " POOL_CHOICE
    case "${POOL_CHOICE:-1}" in
        2) NODE_POOL_TAG="BALANCER_WIFI" ;;
        3) NODE_POOL_TAG="BALANCER_MOBILE" ;;
        *) NODE_POOL_TAG="BALANCER" ;;
    esac

    echo ""
    info "Панель:     $PANEL_IP"
    info "Интерфейс:  $NET_DEV"
    info "Пул:        $NODE_POOL_TAG"
    echo ""
    read -rp "  Всё верно? (y = установить, n = ввести заново, q = выйти): " CONFIRM
    [[ "$CONFIRM" == "q" ]] && { echo "Выход."; exit 0; }
    [[ "$CONFIRM" != "y" ]] && { echo ""; setup_node; return; }
    echo ""

    info "Очищаем остатки прерванной установки..."
    systemctl stop prometheus-node-exporter 2>/dev/null || true
    docker rm -f cadvisor 2>/dev/null || true
    while iptables -D INPUT -p tcp --dport 9100 2>/dev/null; do :; done
    while iptables -D INPUT -p tcp --dport 8080 2>/dev/null; do :; done
    rm -f /usr/local/bin/balancer

    info "Обновляем пакеты..."
    apt-get update -q

    info "Устанавливаем prometheus-node-exporter..."
    apt-get install -y prometheus-node-exporter
    systemctl enable prometheus-node-exporter
    systemctl start prometheus-node-exporter
    systemctl is-active --quiet prometheus-node-exporter \
        && success "node_exporter запущен" \
        || error "node_exporter не запустился"

    if ! command -v docker &>/dev/null; then
        warn "Docker не найден — устанавливаем..."
        curl -fsSL https://get.docker.com | sh
        systemctl enable docker && systemctl start docker
        success "Docker установлен"
    else
        success "Docker уже установлен"
    fi

    info "Запускаем cAdvisor..."
    docker rm -f cadvisor 2>/dev/null || true
    docker run -d --name=cadvisor --restart=always -p 8080:8080 \
        -v /:/rootfs:ro -v /var/run:/var/run:ro -v /sys:/sys:ro \
        -v /var/lib/docker/:/var/lib/docker:ro \
        gcr.io/cadvisor/cadvisor:latest
    sleep 3
    docker ps | grep -q cadvisor \
        && success "cAdvisor запущен" \
        || error "cAdvisor не запустился"

    info "Закрываем порты метрик..."
    DEBIAN_FRONTEND=noninteractive apt-get install -y iptables-persistent
    iptables -I INPUT -p tcp --dport 9100 ! -s "$PANEL_IP" -j DROP
    iptables -I INPUT -p tcp --dport 8080 ! -s "$PANEL_IP" -j DROP
    iptables-save > /etc/iptables/rules.v4
    success "Порты закрыты, доступны только с $PANEL_IP"

    # ── Сохраняем конфиг ноды ─────────────────────────────────────
    mkdir -p /etc/vpn-balancer
    echo "NODE_POOL_TAG=$NODE_POOL_TAG" > /etc/vpn-balancer/node_config
    success "Конфиг ноды сохранён: /etc/vpn-balancer/node_config"

    # ── Textfile collector для node_exporter ──────────────────────
    TEXTFILE_DIR="/var/lib/prometheus/node-exporter"
    mkdir -p "$TEXTFILE_DIR"
    DEFAULTS="/etc/default/prometheus-node-exporter"
    if [ -f "$DEFAULTS" ]; then
        grep -q "textfile" "$DEFAULTS" || \
            sed -i "s|^ARGS=\"\(.*\)\"|ARGS=\"\1 --collector.textfile.directory=$TEXTFILE_DIR\"|" "$DEFAULTS"
    else
        echo "ARGS=\"--collector.textfile.directory=$TEXTFILE_DIR\"" > "$DEFAULTS"
    fi
    systemctl restart prometheus-node-exporter

    # ── Скрипт пинга (каждые 5 мин → Prometheus) ─────────────────
    cat > /etc/vpn-balancer/ping_metrics.sh << 'PING_EOF'
#!/bin/bash
TEXTFILE_DIR="/var/lib/prometheus/node-exporter"
mkdir -p "$TEXTFILE_DIR"
RESULT=$(ping -c 5 -q 77.88.8.8 2>/dev/null | tail -1)
if [ -n "$RESULT" ]; then
    PING_MS=$(echo "$RESULT" | awk -F/ '{printf "%.2f", $5}')
    PING_OK=1
else
    PING_MS=9999
    PING_OK=0
fi
TMP=$(mktemp)
if [ -f "$TEXTFILE_DIR/vpn_metrics.prom" ]; then
    grep -v "vpn_node_ping" "$TEXTFILE_DIR/vpn_metrics.prom" > "$TMP" 2>/dev/null || true
else
    touch "$TMP"
fi
cat >> "$TMP" << EOF
# HELP vpn_node_ping_ms Ping to 77.88.8.8 from node in ms
# TYPE vpn_node_ping_ms gauge
vpn_node_ping_ms $PING_MS
# HELP vpn_node_ping_ok 1 if ping succeeded
# TYPE vpn_node_ping_ok gauge
vpn_node_ping_ok $PING_OK
EOF
mv "$TMP" "$TEXTFILE_DIR/vpn_metrics.prom"
chmod 644 "$TEXTFILE_DIR/vpn_metrics.prom"
PING_EOF
    chmod +x /etc/vpn-balancer/ping_metrics.sh

    # ── Скрипт speedtest (каждые 8ч → файл + Prometheus) ─────────
    cat > /etc/vpn-balancer/speedtest.sh << 'SPD_EOF'
#!/bin/bash
TEXTFILE_DIR="/var/lib/prometheus/node-exporter"
CAPACITY_FILE="/etc/vpn-balancer/node_capacity"
LOG="/var/log/vpn-speedtest.log"
mkdir -p "$TEXTFILE_DIR"

command -v speedtest-cli &>/dev/null || pip3 install speedtest-cli -q 2>/dev/null

echo "$(date): Запускаем 3 теста..." >> "$LOG"
RESULTS=()
for i in 1 2 3; do
    R=$(speedtest-cli --simple --no-pre-allocate 2>/dev/null | awk '/Upload/{print $2}')
    [ -n "$R" ] && RESULTS+=("$R") && echo "  тест $i: $R Mbps" >> "$LOG"
    [ $i -lt 3 ] && sleep 15
done

if [ ${#RESULTS[@]} -eq 0 ]; then
    echo "$(date): все тесты провалились" >> "$LOG"
    exit 1
fi

SUM=0
for r in "${RESULTS[@]}"; do
    SUM=$(awk "BEGIN{printf \"%.2f\", $SUM + $r}")
done
AVG=$(awk "BEGIN{printf \"%.2f\", $SUM / ${#RESULTS[@]}}")
echo "$AVG" > "$CAPACITY_FILE"
echo "$(date): среднее = $AVG Mbps (${#RESULTS[@]} тестов)" >> "$LOG"

TMP=$(mktemp)
[ -f "$TEXTFILE_DIR/vpn_metrics.prom" ] && \
    grep -v "vpn_node_capacity" "$TEXTFILE_DIR/vpn_metrics.prom" > "$TMP" 2>/dev/null || true
cat >> "$TMP" << EOF
# HELP vpn_node_capacity_mbps Upload Mbps from speedtest (avg of 3 tests)
# TYPE vpn_node_capacity_mbps gauge
vpn_node_capacity_mbps $AVG
EOF
mv "$TMP" "$TEXTFILE_DIR/vpn_metrics.prom"
chmod 644 "$TEXTFILE_DIR/vpn_metrics.prom"
SPD_EOF
    chmod +x /etc/vpn-balancer/speedtest.sh

    # ── Cron задачи ────────────────────────────────────────────────
    cat > /etc/cron.d/vpn-node-metrics << 'CRON_EOF'
*/5 * * * * root /etc/vpn-balancer/ping_metrics.sh
0 */8 * * * root /etc/vpn-balancer/speedtest.sh
CRON_EOF

    # ── Первый запуск ──────────────────────────────────────────────
    info "Замеряем пинг до 77.88.8.8..."
    /etc/vpn-balancer/ping_metrics.sh
    PING_RESULT=$(grep "^vpn_node_ping_ms" "$TEXTFILE_DIR/vpn_metrics.prom" 2>/dev/null | awk '{print $2}')
    [ -n "$PING_RESULT" ] && success "Пинг до Яндекс DNS: ${PING_RESULT} ms" || warn "Пинг не получен"

    info "Запускаем speedtest (3 теста × ~30 сек, займёт ~2 минуты)..."
    /etc/vpn-balancer/speedtest.sh \
        && SUCCESS_SPD=$(cat /etc/vpn-balancer/node_capacity 2>/dev/null) \
        && success "Speedtest: ${SUCCESS_SPD} Mbps (среднее из 3 тестов)" \
        || warn "Speedtest не удался — будет использован floor 100 Mbps"

    info "Устанавливаем команду balancer..."
    curl -4 -Ls "$BASE_URL/balancer.sh" -o /usr/local/bin/balancer
    chmod +x /usr/local/bin/balancer

    NODE_IP=$(curl -s --max-time 3 ifconfig.me 2>/dev/null || hostname -I | awk '{print $1}')

    echo ""
    echo -e "  ${BOLD}╔══════════════════════════════════════╗${NC}"
    echo -e "  ${BOLD}║   Нода настроена!                    ║${NC}"
    echo -e "  ${BOLD}╚══════════════════════════════════════╝${NC}"
    echo ""
    echo -e "  Данные для добавления на панели (${BLUE}balancer → пункт 3${NC}):"
    echo ""
    echo -e "    IP ноды:    ${YELLOW}$NODE_IP${NC}"
    echo -e "    Интерфейс:  ${YELLOW}$NET_DEV${NC}"
    echo -e "    Пул:        ${YELLOW}$NODE_POOL_TAG${NC}"
    echo ""
    echo -e "  Не забудь создать ноду и хост в Remnawave, скопировать UUID хоста"
    echo ""
}

# ══════════════════════════════════════════════════════════════
# ГЛАВНОЕ МЕНЮ — вызывается после определения функций
# ══════════════════════════════════════════════════════════════
echo ""
echo -e "  ${BOLD}╔══════════════════════════════════════╗${NC}"
echo -e "  ${BOLD}║     VPN Balancer Setup               ║${NC}"
echo -e "  ${BOLD}╚══════════════════════════════════════╝${NC}"
echo ""
echo -e "  ${BLUE}1)${NC} Установить на панель  ${YELLOW}(Remnawave + Bedolaga уже стоят)${NC}"
echo -e "  ${BLUE}2)${NC} Установить на ноду    ${YELLOW}(новый EU-сервер)${NC}"
echo -e "  ${BLUE}0)${NC} Выйти"
echo ""
read -rp "  Выбор: " SETUP_MODE

case "$SETUP_MODE" in
    1) setup_panel ;;
    2) setup_node  ;;
    0) exit 0      ;;
    *) error "Неверный выбор" ;;
esac
