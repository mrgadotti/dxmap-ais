#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["pyais>=2.5"]
# ///
"""Receive NMEA AIS sentences from AIS-catcher over UDP and report long-distance
(DX) ships to DXMaps, using the datagram format of AIS DX Aggregator.

Wire format (UDP, www.dxmaps.com:7350, ASCII, no newline), recovered from
AisDXAggregator.exe:

    AIS|<call>|<qth_locator>|<qrg>|<mmsi>|<ship_locator>|<rep>|<ver>|<txname>

AIS-catcher side:  AIS-catcher -u 127.0.0.1 7000   (or the UDP output in the web UI)
Settings live in a config file (see dxmaps.conf.example); CLI flags override it.
"""

from __future__ import annotations

import argparse
import configparser
import math
import re
import socket
import sys
import time
from pathlib import Path

from pyais import decode as ais_decode

QRG = {"A": "161975", "B": "162025"}
POSITION_TYPES = {1, 2, 3, 18}  # single-sentence position reports
# type 19 spans two sentences; fragmented messages are ignored (as the original does)
LOCATOR_RE = re.compile(r"^[A-R]{2}[0-9]{2}[A-X]{2}$")
CONFIG_SEARCH = [Path("/etc/dxmaps/dxmaps.conf"), Path(__file__).with_name("dxmaps.conf")]


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def maidenhead(lat: float, lon: float) -> str:
    """6-character Maidenhead locator, e.g. GG87LL."""
    lon += 180.0
    lat += 90.0
    f_lon, f_lat = int(lon // 20), int(lat // 10)
    lon -= f_lon * 20
    lat -= f_lat * 10
    s_lon, s_lat = int(lon // 2), int(lat // 1)
    lon -= s_lon * 2
    lat -= s_lat * 1
    ss_lon, ss_lat = int(lon * 12), int(lat * 24)
    return (
        chr(65 + f_lon) + chr(65 + f_lat) + str(s_lon) + str(s_lat)
        + chr(97 + min(ss_lon, 23)).upper() + chr(97 + min(ss_lat, 23)).upper()
    )


def locator_center(loc: str) -> tuple[float, float]:
    """(lat, lon) of the centre of a 6-character Maidenhead square."""
    loc = loc.upper()
    lon = (-180 + (ord(loc[0]) - 65) * 20 + int(loc[2]) * 2
           + (ord(loc[4]) - 65) * (2 / 24) + (2 / 24) / 2)
    lat = (-90 + (ord(loc[1]) - 65) * 10 + int(loc[3])
           + (ord(loc[5]) - 65) * (1 / 24) + (1 / 24) / 2)
    return lat, lon


def build_datagram(call: str, qth: str, qrg: str, mmsi: int, ship_loc: str,
                   rep: int, ver: str, name: str = "") -> bytes:
    fields = ["AIS", call, qth, qrg, f"{mmsi:09d}", ship_loc, str(rep), ver, name.replace("|", " ")]
    return "|".join(fields).encode("ascii", "replace")


def channel_of(sentence: str) -> str | None:
    parts = sentence.split(",")
    return parts[4] if len(parts) > 6 and parts[4] in QRG else None


class Aggregator:
    def __init__(self, cfg: argparse.Namespace) -> None:
        self.c = cfg
        self.last_pos: dict[int, tuple[float, float, float]] = {}
        self.last_sent: dict[int, float] = {}
        self.out = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.dest = (cfg.dx_host, cfg.dx_port)

    def handle(self, sentence: str) -> None:
        c = self.c
        sentence = sentence.strip()
        if not sentence.startswith("!AIVDM,1,1"):  # unfragmented only
            return
        channel = channel_of(sentence)
        if channel is None:
            return
        try:
            msg = ais_decode(sentence).asdict()
        except Exception as exc:  # malformed / checksum errors
            if c.verbose:
                print(f"decode error: {exc}: {sentence}", flush=True)
            return
        if msg.get("msg_type") not in POSITION_TYPES:
            return
        mmsi = int(msg["mmsi"])
        lat, lon = msg.get("lat"), msg.get("lon")
        rep = int(msg.get("repeat", 0))
        if mmsi == 0 or lat is None or lon is None:
            return
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):  # 91/181 = not available
            return
        if rep > 0 and not c.allow_repeaters:
            return

        now = time.time()
        km = haversine_km(c.lat, c.lon, lat, lon)
        if not (c.min_km <= km <= c.max_km):
            self.last_pos[mmsi] = (now, lat, lon)
            return

        prev = self.last_pos.get(mmsi)
        self.last_pos[mmsi] = (now, lat, lon)
        if prev:  # reject position jumps (>100 km in <30 min), as the original does
            dt_min = (now - prev[0]) / 60
            if dt_min < 30 and haversine_km(prev[1], prev[2], lat, lon) > 100:
                if c.verbose:
                    print(f"MMSI {mmsi}: implausible jump, ignored", flush=True)
                return

        if now - self.last_sent.get(mmsi, 0) < c.resend_min * 60:
            return

        payload = build_datagram(c.callsign, c.locator, QRG[channel], mmsi,
                                 maidenhead(lat, lon), rep, c.version)
        self.last_sent[mmsi] = now
        tag = "DRY-RUN" if c.dry_run else "SEND"
        print(f"{tag} {km:7.1f} km -> {payload.decode()}", flush=True)
        if not c.dry_run:
            self.out.sendto(payload, self.dest)


def load_config(path: Path | None) -> configparser.ConfigParser:
    cp = configparser.ConfigParser(inline_comment_prefixes=("#", ";"))
    candidates = [path] if path else CONFIG_SEARCH
    for cand in candidates:
        if cand.is_file():
            cp.read(cand)
            cp.loaded_from = cand  # type: ignore[attr-defined]
            return cp
    cp.loaded_from = None  # type: ignore[attr-defined]
    return cp


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("-c", "--config", type=Path, help="config file (default: /etc/dxmaps/dxmaps.conf, then ./dxmaps.conf)")
    p.add_argument("--callsign", help="override [station] callsign")
    p.add_argument("--locator", help="override [station] locator")
    p.add_argument("--min-km", type=float, help="override [filters] min_km")
    p.add_argument("--dry-run", action="store_true", help="print datagrams, do not send")
    p.add_argument("-v", "--verbose", action="store_true")
    p.add_argument("--test-ship", nargs=4, metavar=("MMSI", "LAT", "LON", "NAME"),
                   help="send ONE synthetic spot and exit (e.g. 710000001 -22.9 -43.17 FAUSTAO)")
    p.add_argument("--test-channel", choices=sorted(QRG), default="A", help="AIS channel for --test-ship")
    args = p.parse_args()

    cp = load_config(args.config)
    if args.config and cp.loaded_from is None:
        p.error(f"config file not found: {args.config}")

    def get(section: str, key: str, default: str) -> str:
        return cp.get(section, key, fallback=default).strip()

    cfg = argparse.Namespace(
        callsign=(args.callsign or get("station", "callsign", "")).upper(),
        locator=(args.locator or get("station", "locator", "")).upper(),
        listen_host=get("listen", "host", "127.0.0.1"),
        listen_port=int(get("listen", "port", "7000")),
        dx_host=get("dxmaps", "host", "www.dxmaps.com"),
        dx_port=int(get("dxmaps", "port", "7350")),
        version=get("dxmaps", "version", "1.15"),
        min_km=args.min_km if args.min_km is not None else float(get("filters", "min_km", "300")),
        max_km=float(get("filters", "max_km", "3000")),
        resend_min=float(get("filters", "resend_min", "15")),
        allow_repeaters=cp.getboolean("filters", "allow_repeaters", fallback=False),
        dry_run=args.dry_run,
        verbose=args.verbose,
    )

    if not 1 <= len(cfg.callsign) <= 8:
        p.error("callsign missing or longer than 8 characters (set [station] callsign)")
    if not LOCATOR_RE.match(cfg.locator):
        p.error("locator missing or invalid; need 6 characters like GG52TM (set [station] locator)")
    cfg.lat, cfg.lon = locator_center(cfg.locator)

    if args.test_ship:
        mmsi, lat, lon, name = int(args.test_ship[0]), float(args.test_ship[1]), float(args.test_ship[2]), args.test_ship[3]
        km = haversine_km(cfg.lat, cfg.lon, lat, lon)
        payload = build_datagram(cfg.callsign, cfg.locator, QRG[args.test_channel], mmsi,
                                 maidenhead(lat, lon), 0, cfg.version, name)
        print(f"{km:.1f} km -> {payload.decode()} -> {cfg.dx_host}:{cfg.dx_port}"
              f"{' (dry-run)' if cfg.dry_run else ''}", flush=True)
        if not cfg.dry_run:
            socket.socket(socket.AF_INET, socket.SOCK_DGRAM).sendto(payload, (cfg.dx_host, cfg.dx_port))
        return

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.bind((cfg.listen_host, cfg.listen_port))
    except OSError as exc:
        sys.exit(f"cannot bind UDP {cfg.listen_host}:{cfg.listen_port}: {exc}")
    agg = Aggregator(cfg)
    print(f"config={cp.loaded_from or 'defaults'} call={cfg.callsign} locator={cfg.locator} "
          f"listen={cfg.listen_host}:{cfg.listen_port} min={cfg.min_km}km "
          f"{'DRY-RUN' if cfg.dry_run else 'dest=%s:%d' % agg.dest}", flush=True)
    while True:
        data, _ = sock.recvfrom(4096)
        for line in data.decode("ascii", "ignore").splitlines():
            agg.handle(line)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
