# synergy-24802-presence-shim

> WARNING: Experimental, unofficial project. Not affiliated with, endorsed by,
> or supported by Symless or Synergy. Not an official Synergy implementation.
> Use at your own risk.

## Why this project exists

Official Synergy 3 mesh presence expects every peer — including a Linux
host — to answer the mesh presence endpoint on TCP **24802**. On
NixOS/Hyprland the official Synergy core cannot run (it needs
desktop-portal input capture/injection: InputCapture/RemoteDesktop, which
Hyprland does not provide). The working split on such hosts is:

- **TCP 24800 — actual keyboard/mouse input**, handled separately by
  [`waynergy`](https://github.com/...), a Synergy-protocol client that injects
  input through a kernel `uinput` device the compositor reads as an ordinary
  pointer/keyboard. This shim is NOT an alternative to waynergy and does NOT
  carry mouse/keyboard input.
- **TCP 24802 — Synergy mesh presence/sync compatibility**, handled by this
  shim: it answers the HTTP `/ping` health probe and accepts the mesh
  WebSocket, exchanging minimal signed `dbcheck`/`addpeer` messages so the
  mesh UI keeps showing this host as online.

Without the shim, peers log `Unexpected server response: 404` on the
WebSocket upgrade and the host appears offline in the mesh UI — even while
waynergy input works fine. Presence and input are independent; either can be
up while the other is down.

## Port table

| Port      | Owner             | Carries                                              |
|-----------|-------------------|------------------------------------------------------|
| TCP 24800 | waynergy          | Mouse + keyboard input (not provided by this shim)   |
| TCP 24802 | synergy-presence  | HTTP `/ping` + minimal mesh WebSocket/sync compat    |
| UDP 41641 | Tailscale         | WireGuard transport (host firewall/Tailscale config) |
| UDP 40000 | Tailscale         | Peer-relay port if this host relays for others       |

Do NOT expose TCP 24802 to the public Internet. Bind it to LAN/tailnet
addresses and firewall it appropriately.

## Requirements

- Python 3.9+ standard library only. No third-party dependencies.
- A Synergy-licensed `local.json` and a mesh `db.json` on disk (default
  Synergy client config location — see Install). NEVER commit your real
  files: they contain your license serial, computer IDs, and IP addresses.
  `examples/` shows the expected structure with placeholder values only.
- `waynergy` still required for mouse/keyboard input.
- Tailscale (or another VPN/LAN path) still required for connectivity; the
  shim does not provide any VPN.

## Install (clean NixOS/Hyprland host)

1. Copy the script to a documented location:
   `cp synergy-presence ~/.local/bin/synergy-presence && chmod +x
   ~/.local/bin/synergy-presence`
2. Install the user unit:
   `cp systemd/synergy-presence.service.example
   ~/.config/systemd/user/synergy-presence.service`
3. Edit `ExecStart`/`PATH` in the unit if your layout differs (adjust PATH
   per distribution; the example is generic).
4. Ensure Synergy's `local.json` (computer ID, license serial, IPs) and mesh
   `db.json` (version + mesh data) exist, normally under
   `~/.var/app/com.symless.synergy/config/Synergy/`. These hold real
   credentials — never commit them; compare against `examples/`.
5. `systemctl --user daemon-reload && systemctl --user enable --now
   synergy-presence.service`
6. Verify the listener: `ss -tlnp | grep 24802` (expect `0.0.0.0:24802`).
7. Verify `/ping`: `curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:24802/ping`
   (expect `200` with `X-Synergy-Signature`/`X-Synergy-Version` headers).
8. Verify Tailscale reachability to the peer independently
   (`tailscale status`, `tailscale ping`).
9. Verify waynergy separately for 24800 input — presence online does not
   imply input works, and vice versa.

## Configuration

All configuration is via environment variables:

| Variable               | Default   | Meaning                              |
|------------------------|-----------|--------------------------------------|
| `SYNERGY_PRESENCE_BIND`| `0.0.0.0` | Local address to listen on           |
| `SYNERGY_PING_PORT`    | `24802`   | Port to listen on                    |
| `SYNERGY_PRESENCE_DEBUG`| (unset)  | Troubleshooting-only per-message logging (see Logging) |

## What it implements

- `GET /ping` with a signed body (`synergy_version`, hostname, system string).
- RFC 6455 WebSocket upgrade on `/`.
- One `dbcheck` + one `addpeer` hello per new connection.
- State-driven replies to inbound `dbcheck`/`addpeer`: each distinct version
  situation is answered once per connection, then the shim stays silent.
  See `docs/protocol-notes.md`.

## The convergence fix (read this before treating the shim as complete)

The original implementation answered **every** inbound sync message with a
freshly-signed reply and advertised versions frozen at process start. In
production against Synergy 3.6.3 peers this participated in a
`dbcheck`/`addpeer` livelock. Observed consequences:

- thousands of sync messages per second (~2,300 segments/s inbound)
- multi-megabyte/sec traffic (~2.8 MB/s in, ~260 KB/s out, sustained)
- ~76% of one CPU core in the shim (mostly receive-side parse/verify)
- severe queueing/bufferbloat: an unrelated persistent 24800 input
  connection's effective TCP RTT rose from ~12 ms to ~200+ ms
- stopping only the shim returned 24800 RTT to ~12 ms and visibly restored
  pointer responsiveness

The fixed implementation:

- replies once per distinct `dbcheck` state — `(peer syncVersion/schema,
  our syncVersion/schema)` — per connection, then goes silent;
- acknowledges each distinct `addpeer` announcement once per connection;
- tracks the local database version (re-read when `db.json` changes) instead
  of freezing it at startup;
- becomes quiet once state has converged: healthy exchange is
  connect → small handshake → silence.

This does NOT claim Synergy itself is bug-free. It states only that this
compatibility layer no longer amplifies the exchange: either side going
quiet now terminates it. `senddb`/full database convergence remains
intentionally unsupported (remote DB content is never applied locally), so a
peer pushing bulk state it believes is unacknowledged is a residual,
separately-investigated behaviour. See `docs/livelock-investigation.md`.

## Networking

At home, this host and its peers communicate over the local LAN using
Tailscale's direct peer-to-peer path when available (~1 ms class). Tailscale
remains enabled at all times; the shim does not bypass or replace it, and
DERP/peer relay are fallback mechanisms, not required for normal local
communication. For remote clients, this host can act as a Tailscale peer
relay as configured separately — the shim itself provides no VPN.

If the direct path will not establish, suspect the host firewall first
(Tailscale needs its WireGuard UDP port reachable on the LAN interface),
not this shim.

## Troubleshooting

- **Synergy says the Linux peer is offline**: check the listener (`ss -tlnp
  | grep 24802`), then `/ping` (expect 200 + Synergy headers), then identity
  files (missing `local.json` fields make the process exit at startup).
- **24802 not listening**: `systemctl --user status synergy-presence`,
  then the journal; common cause is unreadable/missing identity files.
- **`/ping` wrong response / 404**: something else owns the port, or an old
  build is running — compare the deployed file against this repo.
- **Signature/identity problems**: serial mismatch between peers fails
  verification and messages are dropped (same-license peers share the
  serial); check `local.json` against the licensed host, never commit it.
- **waynergy works but mesh presence is offline**: this shim is down or
  unreachable — input (24800) and presence (24802) fail independently.
- **Mesh presence works but mouse/keyboard does not**: waynergy/24800 issue,
  not this shim — check the waynergy service and its server connection.
- **Unexpectedly high 24802 traffic**: sustained megabytes/sec or hundreds of
  messages/sec is abnormal. Check per-connection byte/segment counters first
  (`ss -tie`), compare both directions, and watch whether either side going
  quiet stops it. Do NOT enable per-message debug logging unless actively
  diagnosing — at storm rates the logs themselves become a load problem.

## Logging

`SYNERGY_PRESENCE_DEBUG=1` enables per-message logging. It is a
troubleshooting-only option and should normally be unset. Do not add a
default polling loop; the shim is entirely reactive outside its one
connect-time hello.

## Limitations / security

- No full database convergence; no remote DB application; minimal protocol
  surface by design.
- Bind and firewall appropriately; never expose 24802 publicly.
- Real Synergy credential/config files (`local.json`, `db.json`, certs,
  fingerprints, logs) must never be committed — `examples/` placeholders
  only.
- Test coverage is a small stdlib `unittest` suite (`tests/`); run it with
  `python3 tests/test_convergence.py`.

## License

MIT. See `LICENSE`.
