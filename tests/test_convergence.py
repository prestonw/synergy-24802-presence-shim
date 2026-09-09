#!/usr/bin/env python3
"""Convergence tests for synergy-presence: dbcheck/addpeer replies are
state-driven (one per distinct state per connection), not unconditional echoes.

Stdlib unittest only. Run from the repo root:

    python3 tests/test_convergence.py
"""
import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def load_module():
    # The script has no .py extension, so importlib cannot infer a loader.
    from importlib.machinery import SourceFileLoader

    path = str(REPO_ROOT / "synergy-presence")
    loader = SourceFileLoader("synergy_presence", path)
    spec = importlib.util.spec_from_file_location(
        "synergy_presence", path, loader=loader
    )
    assert spec is not None
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


mod = load_module()

TEST_ID = "TESTCOMPUTERID0000000000000000000000000000000000000000000000"
TEST_SERIAL = "TEST-SERIAL-NOT-A-REAL-LICENSE"
TEST_SCHEMA = 17
TEST_SYNC = 1000


class FakeSock:
    """Captures raw server->client bytes."""

    def __init__(self):
        self.data = bytearray()

    def sendall(self, chunk):
        self.data.extend(chunk)


def text_frames(raw):
    """Decode unmasked server text frames; return parsed JSON messages."""
    out = []
    i = 0
    raw = bytes(raw)
    while i < len(raw):
        opcode = raw[i] & 0x0F
        ln = raw[i + 1] & 0x7F
        j = i + 2
        if ln == 126:
            ln = int.from_bytes(raw[j : j + 2], "big")
            j += 2
        elif ln == 127:
            ln = int.from_bytes(raw[j : j + 8], "big")
            j += 8
        if opcode == 0x1:
            out.append(json.loads(raw[j : j + ln].decode("utf-8")))
        i = j + ln
    return out


def make_handler(tmp_db):
    setattr(mod, "DB_JSON", tmp_db)
    h = object.__new__(mod.PresenceHandler)
    h.identity = {
        "computer_id": TEST_ID,
        "serial": TEST_SERIAL,
        "schema": TEST_SCHEMA,
        "sync_version": TEST_SYNC,
        "ips": ["192.0.2.10"],
        "hostname": "example-host",
    }
    h._last_replied_pair = None
    h._last_acked_peer = None
    mod.seed_db_versions(TEST_SYNC, TEST_SCHEMA)
    return h


def inbound(mod, msg_type, body):
    return mod.build_sync_message(
        msg_type=msg_type,
        body=body,
        computer_id="PEER" + "0" * 60,
        serial=TEST_SERIAL,
        db_version=TEST_SCHEMA,
    )


class ConvergenceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "db.json"
        self.db.write_text(
            json.dumps(
                {"version": {"syncVersion": TEST_SYNC, "schemaVersion": TEST_SCHEMA}}
            )
        )
        self.h = make_handler(self.db)
        self.sock = FakeSock()

    def tearDown(self):
        self.tmp.cleanup()

    def feed(self, text):
        before = len(text_frames(self.sock.data))
        self.h._ws_on_message(self.sock, "peer", text)
        return text_frames(self.sock.data)[before:]

    def test_dbcheck_equal_versions_replies_once_then_silent(self):
        body = {"syncVersion": TEST_SYNC, "schemaVersion": TEST_SCHEMA}
        first = self.feed(inbound(mod, "dbcheck", body))
        self.assertEqual(len(first), 1)
        self.assertEqual(first[0]["header"]["type"], "dbcheck")
        self.assertEqual(first[0]["body"], body)
        for _ in range(3):
            self.assertEqual(self.feed(inbound(mod, "dbcheck", body)), [])

    def test_dbcheck_new_peer_version_replies_again(self):
        body = {"syncVersion": TEST_SYNC, "schemaVersion": TEST_SCHEMA}
        self.feed(inbound(mod, "dbcheck", body))
        self.feed(inbound(mod, "dbcheck", body))
        changed = {"syncVersion": TEST_SYNC + 1, "schemaVersion": TEST_SCHEMA}
        replies = self.feed(inbound(mod, "dbcheck", changed))
        self.assertEqual(len(replies), 1)
        # Repeat of the new state is silent again.
        self.assertEqual(self.feed(inbound(mod, "dbcheck", changed)), [])

    def test_dbcheck_unparseable_body_still_answered(self):
        replies = self.feed(inbound(mod, "dbcheck", {"unexpected": True}))
        self.assertEqual(len(replies), 1)

    def test_dbcheck_advertises_current_db_versions(self):
        self.db.write_text(
            json.dumps(
                {
                    "version": {
                        "syncVersion": TEST_SYNC + 5,
                        "schemaVersion": TEST_SCHEMA,
                    }
                }
            )
        )
        # Coarse filesystems may stamp rapid rewrites with an identical mtime;
        # force a tick so the reload guard trips deterministically.
        st = self.db.stat()
        os.utime(self.db, ns=(st.st_atime_ns, st.st_mtime_ns + 2_000_000_000))
        body = {"syncVersion": TEST_SYNC, "schemaVersion": TEST_SCHEMA}
        replies = self.feed(inbound(mod, "dbcheck", body))
        self.assertEqual(len(replies), 1)
        self.assertEqual(replies[0]["body"]["syncVersion"], TEST_SYNC + 5)

    def test_addpeer_ack_once_then_silent(self):
        body = {"id": "PEERID", "ips": ["192.0.2.20"]}
        first = self.feed(inbound(mod, "addpeer", body))
        self.assertEqual(len(first), 1)
        self.assertEqual(first[0]["header"]["type"], "addpeer")
        for _ in range(3):
            self.assertEqual(self.feed(inbound(mod, "addpeer", body)), [])

    def test_addpeer_changed_record_reacked(self):
        self.feed(inbound(mod, "addpeer", {"id": "PEERID", "ips": ["192.0.2.20"]}))
        replies = self.feed(
            inbound(mod, "addpeer", {"id": "PEERID", "ips": ["192.0.2.21"]})
        )
        self.assertEqual(len(replies), 1)

    def test_bad_signature_dropped(self):
        text = inbound(
            mod, "dbcheck", {"syncVersion": TEST_SYNC, "schemaVersion": TEST_SCHEMA}
        )
        data = json.loads(text)
        data["header"]["signature"] = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
        self.assertEqual(self.feed(json.dumps(data)), [])

    def test_parse_versions(self):
        self.assertEqual(
            mod._parse_versions({"syncVersion": 5, "schemaVersion": 17}), (5, 17)
        )
        self.assertIsNone(mod._parse_versions({"nope": 1}))
        self.assertIsNone(mod._parse_versions("not-a-dict"))
        self.assertIsNone(mod._parse_versions({"syncVersion": "x", "schemaVersion": 1}))


if __name__ == "__main__":
    unittest.main(verbosity=2)
