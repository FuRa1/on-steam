import json

import pytest

from tvwake.ipwatch import IpWatchError, Watcher, ago

A, B = "37.189.69.85", "176.79.60.54"
SRC = ("s1", "s2", "s3")


class Clock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


def make(cfg, answers, dns=None, clock=None):
    """answers: {source: ip or Exception}; returns (watcher, sent messages)."""
    sent = []

    def fetch(url):
        a = answers[url]
        if isinstance(a, Exception):
            raise a
        return a + "\n"

    w = Watcher(cfg, fetch=fetch, resolve=lambda h: dns or [], wall=clock or Clock(), alert=sent.append, sources=SRC)
    return w, sent


def state(cfg):
    return json.loads(cfg.ipwatch_state_file.read_text())


def test_first_check_reports_and_stores(make_cfg):
    cfg = make_cfg()
    w, sent = make(cfg, {"s1": A, "s2": A, "s3": A})
    assert w.check() == 0
    assert state(cfg)["ip"] == A and len(sent) == 1 and "ip.public" in sent[0] and A in sent[0]


def test_unchanged_is_quiet_and_needs_one_source(make_cfg):
    cfg = make_cfg()
    make(cfg, {"s1": A, "s2": A, "s3": A})[0].check()
    w, sent = make(cfg, {"s1": A, "s2": OSError("down"), "s3": OSError("down")})
    assert w.check() == 0 and sent == []


def test_change_needs_confirmation_and_reports_old_and_new(make_cfg):
    cfg, clock = make_cfg(), Clock()
    make(cfg, {"s1": A, "s2": A, "s3": A}, clock=clock)[0].check()
    clock.t += 33 * 3600
    w, sent = make(cfg, {"s1": B, "s2": OSError("x"), "s3": B}, clock=clock)
    assert w.check() == 0
    assert len(sent) == 1 and "ip.changed" in sent[0] and f"{A} → {B}" in sent[0] and "1 d 9 h" in sent[0]
    st = state(cfg)
    assert st["ip"] == B and [h["ip"] for h in st["history"]] == [A, B]


def test_single_unconfirmed_answer_is_not_a_change(make_cfg):
    cfg = make_cfg()
    make(cfg, {"s1": A, "s2": A, "s3": A})[0].check()
    w, sent = make(cfg, {"s1": B, "s2": OSError("x"), "s3": OSError("x")})
    assert w.check() == 1 and sent == [] and state(cfg)["ip"] == A
    assert "only one answered" in state(cfg)["last_error"]["message"]


def test_garbage_and_private_answers_ignored(make_cfg):
    cfg = make_cfg()
    w, _ = make(cfg, {"s1": "<html>", "s2": "192.168.1.1", "s3": A})
    with pytest.raises(IpWatchError):
        w.public_ip(None)  # only one valid answer for a new address
    assert w.public_ip(A) == A


def test_all_down_fails_without_message(make_cfg):
    cfg = make_cfg()
    w, sent = make(cfg, {s: OSError("down") for s in SRC})
    assert w.check() == 1 and sent == []


def test_dns_stale_warned_once_then_ok(make_cfg):
    cfg = make_cfg(IPWATCH_DNS_HOST="home.example.net")
    ans = {"s1": B, "s2": B, "s3": B}
    w, sent = make(cfg, ans, dns=[A])
    w.check()
    assert len(sent) == 1 and "ip.public" in sent[0] and "dns.stale" in sent[0] and A in sent[0]
    w.check()
    assert len(sent) == 1  # not again for the same address
    w2, sent2 = make(cfg, ans, dns=[B])
    w2.check()
    assert len(sent2) == 1 and "dns.ok" in sent2[0]


def test_ago():
    assert ago(59) == "0 min" and ago(3 * 3600 + 120) == "3 h 2 min" and ago(50 * 3600) == "2 d 2 h"
