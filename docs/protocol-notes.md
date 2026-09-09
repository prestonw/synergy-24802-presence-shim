# Protocol notes: Synergy mesh presence/sync on TCP 24802

These notes describe the wire behaviour this shim approximates, as observed
against Synergy 3.6.3 peers. They are a reverse-engineered sketch for review,
not an official specification.

## Endpoints

- `GET /ping` — health probe. The real service answers 200 with a JSON body
  (`synergy_version`, `hostname`, `system`) plus signed headers:
  `X-Synergy-Message-ID`, `X-Synergy-Computer-ID`, `X-Synergy-Signature`,
  `X-Synergy-DB-Version`. The shim reproduces this exactly.
- `GET /` with `Upgrade: websocket` — the mesh sync channel. Without a 101
  upgrade response, peers log `Unexpected server response: 404`. The shim
  performs a minimal RFC 6455 server handshake (unmasked text frames
  server-to-client, masked frames client-to-server).

## Sync envelope

Every sync message is a JSON text frame:

```json
{
  "header": {
    "id": "<uuid-v4>",
    "type": "dbcheck | addpeer | senddb | ipbroadcast | reset | updateSerial",
    "signature": "<base64>",
    "dbversion": 17,
    "computerID": "YOUR_COMPUTER_ID",
    "receivers": []
  },
  "body": {}
}
```

## Signing

`signature = base64(sha256(message_id + canonical_json(body) + serial))`,
where `canonical_json` uses `,`/`:` separators and `serial` is the shared
license serial. Both mesh peers on the same license share the serial, so each
side can verify the other. The shim verifies inbound signatures and drops
mismatches silently.

## Message types (as implemented by the shim)

| Type           | Direction | Shim behaviour                                    |
|----------------|-----------|---------------------------------------------------|
| `dbcheck`      | both      | Sent once per connection as hello; each inbound one answered with the locally stored `{syncVersion, schemaVersion}` |
| `addpeer`      | both      | Sent once per connection as hello; each inbound one answered with the local `{id, ips}` peer record |
| `senddb`       | inbound   | Ignored: remote DB content never applied locally  |
| `ipbroadcast`  | inbound   | Ignored                                           |
| `reset`        | inbound   | Ignored                                           |
| `updateSerial` | inbound   | Ignored                                           |

## Version fields

- `header.dbversion`: the DB schema version (17 in current meshes).
- `dbcheck` body: `{syncVersion, schemaVersion}`. `syncVersion` advances on
  the real service whenever the mesh DB is written; a correct peer compares
  versions and only transfers state when they differ.

## What the shim does NOT do

- Compare inbound versions against local versions.
- Re-read its config files after startup (identity frozen per process).
- Correlate replies with requests (no `receivers`, no id echo).
- Push or apply full database state.
- Rate-limit, deduplicate, or otherwise converge repeated messages.
