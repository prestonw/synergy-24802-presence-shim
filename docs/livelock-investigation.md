# Sync-livelock investigation (UNDER INVESTIGATION — not a proven bug)

> Status: open. Nothing below is presented as a conclusively proven defect in
> Synergy itself. It is the evidence and reasoning motivating this
> publication, offered for external review.

## Observed symptoms (macOS peer, Synergy 3.6.3 service)

- Approximately 1.3 MB/s inbound and 1.5 MB/s outbound sustained over roughly
  20 minutes (~2.9 GB bidirectional in that window).
- Hundreds of sync messages per second in both directions.
- ~88% CPU in the macOS `synergy-service` process during episodes.
- The macOS service hit V8 heap exhaustion while processing the high-rate
  sync workload.
- Episodes start and stop on their own; the link then sits idle for hours.

## Corroborating evidence (Linux shim side, kernel TCP counters only)

- Historical per-socket totals on the two live 24802 connections: on the
  order of ~1M inbound and ~1M outbound TCP data segments each way —
  near-1:1 symmetry in message *count*, with inbound segments averaging
  larger (~2 KB, multi-segment peer messages) and outbound averaging ~1 KB
  (one packet per shim reply).
- A 60-second passive counter sample during an idle period showed exactly
  zero movement on all counters: when the macOS peer stops sending, the shim
  sends nothing. The shim has no timers and never transmits unsolicited
  after its per-connection hello, so it cannot originate traffic — but it
  answers every inbound `dbcheck`/`addpeer`, always, with a freshly minted
  message ID and valid signature.

## Candidate contributors (unproven, in order of suspicion)

1. **Unconditional 1:1 replies.** The shim keeps no per-connection state and
   performs no version comparison, so each inbound sync message deterministically
   produces one outbound reply — the gain-of-1 echo needed to sustain
   oscillation if the peer also answers inbound sync messages.
2. **Fresh message IDs on every reply.** Replies are semantically identical
   (static versions) but each carries a new UUID and therefore a new valid
   signature. If the peer treats arrival of a new ID as new information
   rather than comparing versions, every reply is a fresh trigger.
3. **Static versions frozen at process start.** The shim's advertised
   `syncVersion` can never advance toward the peer's, so a version-driven
   peer may never reach the "equal, stay silent" state. `senddb` payloads
   that would update the local DB are black-holed, blocking the very
   propagation that could converge the two sides.
4. **Expensive receive path on the peer.** Heap exhaustion on the macOS side
   is consistent with costly per-message work (parse, verify, store, render)
   happening *before* any duplicate suppression — if so, even a convergent
   exchange at this rate would be punishing, and any echo gain ≥ 1 is
   catastrophic. This is inferred from resource symptoms, not from
   peer-side code, which we do not have.

## What would disprove / refine this

- A 60-second passive counter sample *during* a live episode: strictly
  alternating in/out segment growth at equal rates would confirm ping-pong;
  one-sided growth would refute it.
- Message-type/frequency metadata (no payloads) on the macOS side showing
  whether each shim reply is followed within milliseconds by a fresh peer
  `dbcheck`.
- Testing the minimal conceptual fix (per-connection duplicate suppression
  keyed on inbound versions + re-reading `db.json` per reply) and observing
  whether episodes stop recurring.

## Deliberately not claimed

- That the defect is in Synergy rather than in this shim.
- That fresh IDs alone, or static versions alone, cause the loop.
- Any statement about peer-side internals beyond observed resource symptoms.

## Update: convergence fix deployed (branch `fix/convergent-sync`, PR #1)

The minimal fix was implemented and deployed to production after review:

- `dbcheck`: reply at most once per distinct (peer-version, our-version)
  pair per connection; repeats suppressed; unparseable bodies fail open once.
- `addpeer`: each distinct announcement acknowledged at most once per
  connection.
- Advertised versions track `db.json` via mtime-guarded reload (no polling;
  last-known-good kept on read/parse failure).
- Signature verification, framing, `/ping`, hello, env vars unchanged;
  `senddb` family still ignored by design.

Measured production impact (kernel TCP counters, 60 s windows):

- Before: ~2.83 MB/s inbound (2,304 seg/s), ~262 KB/s outbound (224/s),
  shim at ~76% of one core; unrelated persistent 24800 input connection at
  ~203 ms effective RTT vs ~11 ms floor.
- Shim stopped: 24802 traffic zero; 24800 RTT back to ~12 ms; reporter
  observed substantially improved pointer responsiveness. 24800/waynergy
  never dropped (same TCP session throughout).
- After deploying the fix: listener back, shim idle at 0% CPU awaiting the
  peer's next natural reconnect; convergence proof pending that reconnect
  (tracked by passive counter sampling, no payload capture).
