#!/usr/bin/env bash
# Install the DXMaps AIS reporter on a Raspberry Pi (Raspberry Pi OS / Debian).
# Usage: sudo ./install.sh        (run from the directory containing this script)
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP=/opt/dxmaps
CONF_DIR=/etc/dxmaps
CONF=$CONF_DIR/dxmaps.conf
STATE=/var/lib/dxmaps
USER_NAME=dxmaps

if [[ $EUID -ne 0 ]]; then
  echo "Run as root: sudo $0" >&2
  exit 1
fi

for f in ais_dxmaps.py dxmaps.service dxmaps.conf.example; do
  [[ -f $SRC/$f ]] || { echo "Missing $SRC/$f" >&2; exit 1; }
done

echo "==> Installing system packages"
apt-get update -qq
apt-get install -y -qq curl ca-certificates

echo "==> Installing uv (if missing)"
if [[ ! -x /usr/local/bin/uv ]]; then
  curl -LsSf https://astral.sh/uv/install.sh | env UV_UNMANAGED_INSTALL=/usr/local/bin sh
fi
/usr/local/bin/uv --version

echo "==> Creating service user and directories"
id "$USER_NAME" &>/dev/null || useradd --system --home-dir "$STATE" --create-home --shell /usr/sbin/nologin "$USER_NAME"
install -d -o root -g root -m 755 "$APP" "$CONF_DIR"
install -d -o "$USER_NAME" -g "$USER_NAME" -m 750 "$STATE"
install -m 755 "$SRC/ais_dxmaps.py" "$APP/ais_dxmaps.py"
install -m 644 "$SRC/dxmaps.conf.example" "$CONF_DIR/dxmaps.conf.example"

echo "==> Station configuration"
if [[ -f $CONF ]]; then
  echo "Keeping existing $CONF"
else
  CALL=${DXMAPS_CALLSIGN:-}
  LOC=${DXMAPS_LOCATOR:-}
  if [[ -t 0 ]]; then
    [[ -n $CALL ]] || read -rp "Callsign / station id (max 8 chars): " CALL
    [[ -n $LOC ]] || read -rp "6-character grid locator (e.g. GG52TM): " LOC
  fi
  CALL=${CALL^^}; LOC=${LOC^^}
  if [[ -z $CALL || ! $LOC =~ ^[A-R]{2}[0-9]{2}[A-X]{2}$ ]]; then
    echo "Callsign/locator not provided or invalid; copying the example. Edit $CONF before starting." >&2
    CALL=PU5FON; LOC=GG52TM; NEEDS_EDIT=1
  fi
  sed -e "s/^callsign = .*/callsign = $CALL/" -e "s/^locator  = .*/locator  = $LOC/" \
    "$SRC/dxmaps.conf.example" > "$CONF"
  chmod 644 "$CONF"
fi

echo "==> Pre-fetching Python dependencies"
# Run from a directory the service user can read: uv looks for a project in the
# current directory, and the checkout (e.g. under /home/<user>) may be private.
cd "$APP"
runuser -u "$USER_NAME" -- env HOME="$STATE" UV_CACHE_DIR="$STATE/.cache/uv" \
  /usr/local/bin/uv run --no-project --script "$APP/ais_dxmaps.py" --help >/dev/null

echo "==> Installing systemd service"
install -m 644 "$SRC/dxmaps.service" /etc/systemd/system/dxmaps.service
systemctl daemon-reload
systemctl enable dxmaps.service

if [[ -n ${NEEDS_EDIT:-} ]]; then
  echo
  echo "Service enabled but NOT started. Edit $CONF, then: sudo systemctl start dxmaps"
else
  systemctl restart dxmaps.service
  echo
  echo "Service started."
fi
echo "Logs:   journalctl -u dxmaps -f"
echo "Config: $CONF  (restart with: sudo systemctl restart dxmaps)"
echo "Point AIS-catcher UDP output to the host/port in [listen] (default 127.0.0.1:7000)."
