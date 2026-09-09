# synergy-24802-presence-shim

> WARNING: Experimental, unofficial project. Not affiliated with, endorsed by,
> or supported by Symless or Synergy. Not an official Synergy implementation.
> Use at your own risk.

A minimal mesh-presence compatibility shim for running a Synergy 3 mesh peer on
Linux compositors (NixOS/Hyprland) where the official Synergy core cannot run.

## Background

The official Synergy core requires desktop-portal input capture/injection
(InputCapture/RemoteDesktop), which is unavailable under Hyprland. The working
setup on such hosts is split:

- **Input sharing** is handled separately by `waynergy` (a Synergy-protocol
  client speaking to the mesh server on TCP **24800**).
- **Mesh presence** is handled by this shim on TCP **24802**: it answers the
  HTTP `/ping` health probe and accepts the mesh WebSocket, exchanging minimal
  signed `dbcheck`/`addpeer` sync messages so the mesh UI keeps showing this
  host as online.

Without the shim, peers log `Unexpected server response: 404` on the
WebSocket upgrade and the host appears offline in the mesh UI.

## Requirements

- Python 3.9+ standard library only. No third-party dependencies.
- A Synergy-licensed `local.json` and a mesh `db.json` on disk (see
  `examples/`). NEVER commit your real files — they contain your license
  serial, computer IDs, and IP addresses.

## Install

```sh
cp synergy-presence ~/.local/bin/synergy-presence
chmod +x ~/.local/bin/synergy-presence
cp systemd/synergy-presence.service.example \
   ~/.config/systemd/user/synergy-presence.service
# Edit ExecStart/PATH inside the unit if your layout differs, then:
systemctl --user daemon-reload
systemctl --user enable --now synergy-presence.service
```

## Configuration

All configuration is via environment variables:

| Variable               | Default   | Meaning                              |
|------------------------|-----------|--------------------------------------|
| `SYNERGY_PRESENCE_BIND`| `0.0.0.0` | Local address to listen on           |
| `SYNERGY_PING_PORT`    | `24802`   | Port to listen on                    |
| `SYNERGY_PRESENCE_DEBUG`| (unset)  | Set to `1` for per-message logging   |

Identity (computer ID, license serial, IPs, DB versions) is read from
`~/.var/app/com.symless.synergy/config/Synergy/local.json` and `db.json`
(the default Synergy client config location). See `examples/` for the
expected structure with placeholder values.

## What it implements

- `GET /ping` with a signed body (`synergy_version`, hostname, system string).
- RFC 6455 WebSocket upgrade on `/`.
- One `dbcheck` + one `addpeer` hello per new connection.
- A 1:1 reply to each inbound `dbcheck`/`addpeer` using the locally stored
  versions. See `docs/protocol-notes.md`.

## Known limitations

- It does **not** implement full database convergence: versions are read once
  at startup and never advance; inbound versions are never compared.
- `senddb`, `ipbroadcast`, `reset`, and `updateSerial` are ignored entirely:
  remote DB content is never applied locally.
- Every reply mints a fresh message ID. Replies are cryptographically valid
  but semantically identical, repeated indefinitely.
- One thread per connection; idle connections are never reaped by the shim.
- Under investigation: a sync-message livelock with real Synergy 3.6.3 peers
  at high message rates. See `docs/livelock-investigation.md`. This project is
  published in part to get review of that behaviour.

## License

MIT. See `LICENSE` (fill in the copyright holder before publishing).
