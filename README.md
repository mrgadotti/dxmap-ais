# DXMaps AIS reporter

[Português](README.pt-BR.md)

Receives NMEA AIS sentences from [AIS-catcher](https://github.com/jvde-github/AIS-catcher) over UDP and reports
long-distance ("DX") ships to [DXMaps](https://www.dxmaps.com/aisdxaggregator.html). It is a drop-in replacement
for the Windows program *AIS DX Aggregator*, built for a Raspberry Pi.

## How it works

```
AIS receiver ──> AIS-catcher ──UDP/NMEA──> ais_dxmaps.py ──UDP──> www.dxmaps.com:7350
                 (-u 127.0.0.1 7000)       (distance filter)
```

For each position report (message types 1, 2, 3 and 18) the script computes the distance from your station. Ships at
least `min_km` away are sent to DXMaps as one UDP datagram:

```
AIS|<callsign>|<your locator>|<qrg>|<mmsi>|<ship locator>|<rep>|<version>|<name>
AIS|PU5FON|GG52TM|161975|710000001|GG87JC|0|1.15|
```

This format was recovered by reverse engineering `AisDXAggregator.exe` (kept in `original/`) and confirmed with a
live test. The `qrg` is `161975` for AIS channel A and `162025` for channel B. Live spots leave the name empty.

Filters applied (same idea as the original program): valid latitude/longitude, MMSI not zero, repeated
(relayed) messages ignored unless allowed, position jumps over 100 km in under 30 minutes rejected, and at most one
spot per MMSI every `resend_min` minutes. Fragmented (multi-sentence) messages, including type 19, are ignored.

## Install on a Raspberry Pi

Requires Raspberry Pi OS or Debian with systemd and internet access.

```bash
scp -r dxmaps pi@raspberrypi.local:~/      # or copy the folder any way you like
ssh pi@raspberrypi.local
cd ~/dxmaps
sudo ./install.sh
```

The installer will:

1. install `curl` and [uv](https://docs.astral.sh/uv/) (to `/usr/local/bin`);
2. create the `dxmaps` system user;
3. copy the script to `/opt/dxmaps/` and ask for your callsign and grid locator, writing `/etc/dxmaps/dxmaps.conf`
   (an existing file is never overwritten);
4. download the Python dependency (`pyais`) once;
5. install, enable and start the `dxmaps` systemd service.

For unattended installs: `sudo DXMAPS_CALLSIGN=PU5FON DXMAPS_LOCATOR=GG52TM ./install.sh`.

Then point AIS-catcher at the script. On the command line add `-u 127.0.0.1 7000`; in the web UI add a UDP output
with host `127.0.0.1` and port `7000` (NMEA format).

## Configuration

`/etc/dxmaps/dxmaps.conf` (template: `dxmaps.conf.example`). Restart after changes: `sudo systemctl restart dxmaps`.

| Section | Key | Default | Meaning |
|---|---|---|---|
| `[station]` | `callsign` | – | Your callsign / station id, 1–8 characters |
| `[station]` | `locator` | – | Your 6-character Maidenhead locator (e.g. `GG52TM`) |
| `[listen]` | `host`, `port` | `127.0.0.1`, `7000` | UDP address where AIS-catcher sends |
| `[dxmaps]` | `host`, `port` | `www.dxmaps.com`, `7350` | Destination |
| `[dxmaps]` | `version` | `1.15` | Version string sent to DXMaps |
| `[filters]` | `min_km` | `300` | Minimum distance to report |
| `[filters]` | `max_km` | `3000` | Larger distances are treated as decoding errors |
| `[filters]` | `resend_min` | `15` | Minutes between spots of the same MMSI |
| `[filters]` | `allow_repeaters` | `false` | Also report relayed messages |

Your position is taken as the centre of your locator square (about ±4 km at 6 characters).

## Operation

```bash
systemctl status dxmaps          # state
journalctl -u dxmaps -f          # live log: one "SEND ..." line per reported ship
sudo systemctl restart dxmaps    # after editing the config
```

Test without installing (needs [uv](https://docs.astral.sh/uv/)):

```bash
cp dxmaps.conf.example dxmaps.conf          # edit callsign and locator
uv run ais_dxmaps.py --dry-run -v           # print what would be sent
uv run ais_dxmaps.py --test-ship 710000001 -22.90 -43.17 TESTSHIP   # send ONE fake spot (real send!)
```

`--test-ship` publishes a fictitious ship on the public map; use it sparingly and with a made-up MMSI.

## Uninstall

```bash
sudo systemctl disable --now dxmaps
sudo rm -rf /etc/systemd/system/dxmaps.service /opt/dxmaps /etc/dxmaps /var/lib/dxmaps
sudo userdel dxmaps
```

## Files

| File | Purpose |
|---|---|
| `ais_dxmaps.py` | The reporter (single file, dependencies declared inline for uv) |
| `dxmaps.conf.example` | Configuration template |
| `dxmaps.service` | systemd unit |
| `install.sh` | Raspberry Pi installer |
| `original/` | The original Windows program and an earlier Python test script |
