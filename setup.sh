#!/bin/bash
# ============================================================
#  VPN Balancer — единый установщик
#  Запуск: bash <(curl -4 -Ls "https://raw.githubusercontent.com/FnoUp/vps_installer/main/setup.sh")
# ============================================================

BASE_URL="https://raw.githubusercontent.com/FnoUp/vps_installer/main"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; BOLD='\033[1m'; NC='\033[0m'

info()    { echo -e "  ${BLUE}[INFO]${NC} $1"; }
success() { echo -e "  ${GREEN}[OK]${NC} $1"; }
warn()    { echo -e "  ${YELLOW}[WARN]${NC} $1"; }
error()   { echo -e "  ${RED}[ERROR]${NC} $1"; exit 1; }

# MODE=panel|node — неинтерактивный запуск (например VPN Node Manager):
# пропускает меню и все вопросы ниже, значения берутся из окружения.
# Без MODE поведение полностью прежнее (интерактивный curl|bash).
MODE="${MODE:-}"

# /dev/tty недоступен в неинтерактивной SSH-сессии (paramiko exec_command
# без pty) — exec туда там просто упадёт. Нужен только для ручного curl|bash.
if [ ! -t 0 ] && [ -z "$MODE" ]; then exec < /dev/tty; fi
if [ "$EUID" -ne 0 ]; then error "Запусти от root"; fi

# ask_or_skip <var_name> <prompt> — спрашивает read только если MODE пуст
# и переменная ещё не задана; в неинтерактивном режиме (MODE=panel|node)
# без значения просто предупреждает и продолжает, вместо зависания на read.
ask_or_skip() {
    local -n _ref="$1"
    local _prompt="$2"
    [ -n "$_ref" ] && return
    if [ -n "$MODE" ]; then
        warn "$1 не задан — продолжаю без него"
        return
    fi
    read -rp "$_prompt" _ref
}

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
    BD_ENV="/opt/bedolaga/.env"

    # Значения могут прийти из окружения (неинтерактивный вызов) — тогда
    # соответствующий вопрос/автоопределение ниже пропускается.
    BALANCER_NAME="${BALANCER_NAME:-}"; BALANCER_TAG="${BALANCER_TAG:-}"
    DOMAIN="${DOMAIN:-}"
    TG_TOKEN="${TG_TOKEN:-}"; TG_ERR_CHAT="${TG_ERR_CHAT:-}"; TG_ERR_TOP="${TG_ERR_TOP:-}"
    TG_REP_CHAT="${TG_REP_CHAT:-}"; TG_REP_TOP="${TG_REP_TOP:-}"
    TG_MET_CHAT="${TG_MET_CHAT:-}"; TG_MET_TOP="${TG_MET_TOP:-}"
    RW_TOKEN="${RW_TOKEN:-}"; RW_COOKIE="${RW_COOKIE:-}"

    # ── Имя и тег балансировщика ───────────────────────────────
    echo -e "  ${YELLOW}Если балансировщиков будет несколько (EU + US), дай каждому уникальное имя${NC}"
    [ -n "$BALANCER_NAME" ] || [ -n "$MODE" ] || read -rp "  Имя балансировщика (Enter = vpn-balancer): " BALANCER_NAME
    BALANCER_NAME="${BALANCER_NAME:-vpn-balancer}"
    SVC_NAME=$(echo "$BALANCER_NAME" | tr ' ' '-' | tr '[:upper:]' '[:lower:]')

    [ -n "$BALANCER_TAG" ] || [ -n "$MODE" ] || read -rp "  Тег в Remnawave (Enter = BALANCER): " BALANCER_TAG
    BALANCER_TAG="${BALANCER_TAG:-BALANCER}"
    echo ""
    success "Сервис:  $SVC_NAME"
    success "Тег:     $BALANCER_TAG"
    echo ""

    # ── Remnawave домен ────────────────────────────────────────
    if [ -z "$DOMAIN" ] && [ -f "$RW_ENV" ]; then
        DOMAIN=$(get_env "$RW_ENV" "FRONT_END_DOMAIN")
        [ -n "$DOMAIN" ] && success "Домен панели: $DOMAIN"
    fi
    if [ -z "$DOMAIN" ]; then
        if [ -n "$MODE" ]; then
            error "DOMAIN не задан и не найден в $RW_ENV — передайте DOMAIN явно"
        fi
        read -rp "  Домен панели (например panel.example.com): " DOMAIN
    fi

    # ── Bedolaga TG настройки (авточтение, только для того что ещё не задано) ──
    if [ -f "$BD_ENV" ]; then
        [ -n "$TG_TOKEN"    ] || TG_TOKEN=$(get_env    "$BD_ENV" "BOT_TOKEN")
        [ -n "$TG_ERR_CHAT" ] || TG_ERR_CHAT=$(get_env "$BD_ENV" "ADMIN_NOTIFICATIONS_CHAT_ID")
        [ -n "$TG_ERR_TOP"  ] || TG_ERR_TOP=$(get_env  "$BD_ENV" "ADMIN_NOTIFICATIONS_ERRORS_TOPIC_ID")
        [ -n "$TG_REP_CHAT" ] || TG_REP_CHAT=$(get_env "$BD_ENV" "ADMIN_REPORTS_CHAT_ID")
        [ -n "$TG_REP_TOP"  ] || TG_REP_TOP=$(get_env  "$BD_ENV" "ADMIN_REPORTS_TOPIC_ID")
        [ -n "$TG_TOKEN"    ] && success "TG Bot Token найден автоматически"
        [ -n "$TG_ERR_CHAT" ] && success "TG Errors Chat: $TG_ERR_CHAT (topic: $TG_ERR_TOP)"
        [ -n "$TG_REP_CHAT" ] && success "TG Reports Chat: $TG_REP_CHAT (topic: $TG_REP_TOP)"
    fi

    ask_or_skip TG_TOKEN    "  TG Bot Token: "
    ask_or_skip TG_ERR_CHAT "  TG Errors Chat ID: "
    ask_or_skip TG_ERR_TOP  "  TG Errors Topic ID: "
    ask_or_skip TG_REP_CHAT "  TG Reports Chat ID: "
    ask_or_skip TG_REP_TOP  "  TG Reports Topic ID: "

    # ── Отдельный чат метрик балансировщика ───────────────────
    echo ""
    echo -e "  ${YELLOW}Создай отдельный чат/топик в Telegram только для балансировщика${NC}"
    ask_or_skip TG_MET_CHAT "  Metrics Chat ID: "
    ask_or_skip TG_MET_TOP  "  Metrics Topic ID (0 если без топиков): "
    TG_MET_TOP="${TG_MET_TOP:-0}"

    # ── Remnawave API токен ────────────────────────────────────
    echo ""
    echo -e "  ${YELLOW}Создай API токен: панель → Settings → API Tokens${NC}"
    ask_or_skip RW_TOKEN "  Remnawave API Token: "

    # ── Cookie из nginx ────────────────────────────────────────
    if [ -z "$RW_COOKIE" ]; then
        NGINX_CONF=$(find /opt/remnawave -name "*.conf" 2>/dev/null | head -1)
        if [ -n "$NGINX_CONF" ]; then
            COOKIE_VAL=$(grep -oP 'tufLczDD=\K\S+' "$NGINX_CONF" 2>/dev/null | head -1)
            [ -n "$COOKIE_VAL" ] && RW_COOKIE="tufLczDD=$COOKIE_VAL" && success "Cookie найдена автоматически"
        fi
    fi
    if [ -z "$RW_COOKIE" ]; then
        if [ -n "$MODE" ]; then
            warn "Cookie не задана и не найдена автоматически — продолжаю без неё"
        else
            read -rp "  Cookie (tufLczDD=..., Enter если не нужна): " RW_COOKIE
        fi
    fi

    # ── Подтверждение ──────────────────────────────────────────
    echo ""
    echo -e "  ── Параметры ──────────────────────────────────────────"
    info "Сервис:        $SVC_NAME  |  Тег: $BALANCER_TAG"
    info "Домен:         $DOMAIN"
    info "Metrics Chat:  $TG_MET_CHAT (topic: $TG_MET_TOP)"
    info "Errors Chat:   $TG_ERR_CHAT (topic: $TG_ERR_TOP)"
    info "Reports Chat:  $TG_REP_CHAT (topic: $TG_REP_TOP)"
    echo "  ───────────────────────────────────────────────────────"
    echo ""
    if [ -n "$MODE" ]; then
        info "MODE=panel — пропускаю подтверждение"
    else
        read -rp "  Всё верно? (y/n): " CONFIRM
        [[ "$CONFIRM" != "y" ]] && { echo "Отмена."; exit 0; }
    fi

    INSTALL_DIR="/opt/$SVC_NAME"
    LOG_DIR="/var/log/$SVC_NAME"

    echo ""
    info "Создаём директории..."
    mkdir -p "$INSTALL_DIR" "$LOG_DIR" /etc/prometheus/targets

    info "Скачиваем balancer.py..."
    curl -4 -Ls --max-time 30 "$BASE_URL/balancer_template.py" -o "$INSTALL_DIR/balancer.py" \
        || error "Не удалось скачать balancer_template.py с GitHub"
    [ ! -s "$INSTALL_DIR/balancer.py" ] && error "Скачанный файл пустой — проверь URL в BASE_URL"

    info "Заполняем конфиг..."
    sed -i \
        -e "s|%%BALANCER_NAME%%|$BALANCER_NAME|g" \
        -e "s|%%BALANCER_TAG%%|$BALANCER_TAG|g" \
        -e "s|%%SVC_NAME%%|$SVC_NAME|g" \
        -e "s|%%DOMAIN%%|$DOMAIN|g" \
        -e "s|%%RW_TOKEN%%|$RW_TOKEN|g" \
        -e "s|%%RW_COOKIE%%|$RW_COOKIE|g" \
        -e "s|%%TG_TOKEN%%|$TG_TOKEN|g" \
        -e "s|%%TG_METRICS_CHAT%%|$TG_MET_CHAT|g" \
        -e "s|%%TG_METRICS_TOPIC%%|$TG_MET_TOP|g" \
        -e "s|%%TG_ERRORS_CHAT%%|$TG_ERR_CHAT|g" \
        -e "s|%%TG_ERRORS_TOPIC%%|$TG_ERR_TOP|g" \
        -e "s|%%TG_REP_CHAT%%|$TG_REP_CHAT|g" \
        -e "s|%%TG_REP_TOPIC%%|$TG_REP_TOP|g" \
        "$INSTALL_DIR/balancer.py"

    info "Создаём targets файлы..."
    for f in vpn_nodes.yml docker.yml ping.yml; do
        [ ! -f "/etc/prometheus/targets/$f" ] && echo "" > "/etc/prometheus/targets/$f"
    done

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
StandardOutput=append:$LOG_DIR/balancer.log
StandardError=append:$LOG_DIR/balancer.log

[Install]
WantedBy=multi-user.target
EOF

    systemctl daemon-reload
    systemctl enable "$SVC_NAME"
    systemctl start "$SVC_NAME"

    info "Проверяем python3 и зависимости..."
    command -v python3 &>/dev/null || apt-get install -y python3
    python3 -c "import requests" 2>/dev/null || apt-get install -y python3-requests

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

    PANEL_IP="${PANEL_IP:-}"
    NET_DEV="${NET_DEV:-}"

    [ -n "$PANEL_IP" ] || [ -n "$MODE" ] || read -rp "  IP адрес панели (Remnawave): " PANEL_IP
    [ -z "$PANEL_IP" ] && error "IP панели не может быть пустым (передайте PANEL_IP)"

    if [ -n "$NET_DEV" ]; then
        info "Интерфейс задан явно: ${YELLOW}$NET_DEV${NC}"
    else
    # ── Автоопределение интерфейса ─────────────────────────────
    IFACES=()
    while IFS= read -r iface; do
        IFACES+=("$iface")
    done < <(ip -o link show | awk -F': ' '{print $2}' | grep -vE '^lo$|^docker|^veth|^br-|^virbr')

    if [ "${#IFACES[@]}" -eq 0 ]; then
        warn "Не удалось определить интерфейс, используем ens3"
        NET_DEV="ens3"
    elif [ "${#IFACES[@]}" -eq 1 ]; then
        NET_DEV="${IFACES[0]}"
        IFACE_IP=$(ip -4 addr show "$NET_DEV" 2>/dev/null | grep -oP '(?<=inet\s)\d+\.\d+\.\d+\.\d+' | head -1)
        info "Найден интерфейс: ${YELLOW}$NET_DEV${NC} (IP: ${IFACE_IP:-нет IP})"
        if [ -z "$MODE" ]; then
            read -rp "  Использовать его? (Enter = да, или введи другой): " NET_DEV_INPUT
            NET_DEV="${NET_DEV_INPUT:-$NET_DEV}"
        fi
    else
        echo ""
        info "Найдено несколько интерфейсов:"
        for i in "${!IFACES[@]}"; do
            IFACE="${IFACES[$i]}"
            IFACE_IP=$(ip -4 addr show "$IFACE" 2>/dev/null | grep -oP '(?<=inet\s)\d+\.\d+\.\d+\.\d+' | head -1)
            echo "    $((i+1))) $IFACE   ${IFACE_IP:-нет IP}"
        done
        echo ""
        if [ -z "$MODE" ]; then
            read -rp "  Выбери номер (Enter = 1): " IFACE_NUM
        fi
        IFACE_NUM="${IFACE_NUM:-1}"
        if [[ "$IFACE_NUM" =~ ^[0-9]+$ ]] && [ "$IFACE_NUM" -ge 1 ] && [ "$IFACE_NUM" -le "${#IFACES[@]}" ]; then
            NET_DEV="${IFACES[$((IFACE_NUM-1))]}"
        else
            NET_DEV="${IFACES[0]}"
        fi
    fi
    fi

    echo ""
    info "Панель:     $PANEL_IP"
    info "Интерфейс:  $NET_DEV"
    echo ""
    if [ -n "$MODE" ]; then
        info "MODE=node — пропускаю подтверждение"
    else
        read -rp "  Всё верно? (y/n): " CONFIRM
        [[ "$CONFIRM" != "y" ]] && { echo "Отмена."; exit 0; }
    fi
    echo ""

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
    apt-get install -y iptables-persistent
    iptables -D INPUT -p tcp --dport 9100 -j DROP 2>/dev/null || true
    iptables -D INPUT -p tcp --dport 8080 -j DROP 2>/dev/null || true
    iptables -I INPUT -p tcp --dport 9100 ! -s "$PANEL_IP" -j DROP
    iptables -I INPUT -p tcp --dport 8080 ! -s "$PANEL_IP" -j DROP
    iptables-save > /etc/iptables/rules.v4
    success "Порты закрыты, доступны только с $PANEL_IP"

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
    echo ""
    echo -e "  Не забудь создать ноду и хост в Remnawave, скопировать UUID хоста"
    echo ""
}

# ══════════════════════════════════════════════════════════════
# ГЛАВНОЕ МЕНЮ — вызывается после определения функций
# ══════════════════════════════════════════════════════════════
if [ -n "$MODE" ]; then
    case "$MODE" in
        panel) SETUP_MODE=1 ;;
        node)  SETUP_MODE=2 ;;
        *) error "Неверный MODE: '$MODE' (ожидается panel|node)" ;;
    esac
else
    clear
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
fi

case "$SETUP_MODE" in
    1) setup_panel ;;
    2) setup_node  ;;
    0) exit 0      ;;
    *) error "Неверный выбор" ;;
esac
