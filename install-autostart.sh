#!/bin/bash
# Встановлює systemd-юніт автозапуску стеку mcp-1c-v1 при старті Ubuntu.
#   sudo ./install-autostart.sh            — встановити й увімкнути
#   sudo ./install-autostart.sh --remove   — вимкнути й видалити
set -euo pipefail

UNIT=mcp-1c-v1.service
DEST=/etc/systemd/system/$UNIT
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN_USER="${SUDO_USER:-$(stat -c %U "$DIR")}"

if [[ $EUID -ne 0 ]]; then
    echo "Потрібні права root: sudo $0 $*" >&2
    exit 1
fi

if [[ "${1:-}" == "--remove" ]]; then
    systemctl disable "$UNIT" 2>/dev/null || true
    rm -f "$DEST"
    systemctl daemon-reload
    echo "✅ Автозапуск вимкнено, $DEST видалено (контейнери не зупинялись)."
    exit 0
fi

if ! id -nG "$RUN_USER" | tr ' ' '\n' | grep -qx docker; then
    echo "⚠️  Користувач $RUN_USER не в групі docker — юніт не зможе керувати контейнерами." >&2
    exit 1
fi

sed -e "s|@USER@|$RUN_USER|g" -e "s|@DIR@|$DIR|g" "$DIR/systemd/$UNIT" > "$DEST"
chmod 644 "$DEST"
systemctl daemon-reload
systemctl enable docker.service >/dev/null
systemctl enable --now "$UNIT"

echo "✅ $UNIT встановлено й увімкнено (користувач $RUN_USER, тека $DIR)."
systemctl --no-pager status "$UNIT" | head -n 12
