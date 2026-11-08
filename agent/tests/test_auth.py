import pytest

from tvwake.auth import AuthError, Verifier, sign
from tvwake.wol import magic_packet

def test_sign_matches_definition():
    import hashlib
    import hmac

    secret = "test-secret-0123456789abcdef0123456789"
    msg = b"POST\n/wake\n1700000000\n"
    want = hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest()
    assert sign(secret, "post", "/wake", "1700000000", "") == want


def make(now=1_700_000_000.0):
    t = {"now": now}
    return Verifier("s" * 40, wall=lambda: t["now"]), t


def signed(ts="1700000000", method="GET", path="/jobs/1", body=b""):
    return method, path, ts, sign("s" * 40, method, path, ts, body), body


def test_accepts_valid():
    v, _ = make()
    v.verify(*signed())


def test_rejects_bad_signature():
    v, _ = make()
    m, p, ts, sig, body = signed()
    with pytest.raises(AuthError, match="bad signature"):
        v.verify(m, p, ts, "0" * 64, body)


def test_signature_covers_path_and_body():
    v, _ = make()
    m, p, ts, sig, body = signed(method="POST", path="/wake", body=b"{}")
    with pytest.raises(AuthError):
        v.verify(m, "/jobs/x", ts, sig, body)
    with pytest.raises(AuthError):
        v.verify(m, p, ts, sig, b"{ }")


@pytest.mark.parametrize("delta", [61, -61, 3600])
def test_rejects_timestamp_outside_window(delta):
    v, _ = make(now=1_700_000_000.0 + delta)
    with pytest.raises(AuthError, match="window"):
        v.verify(*signed())


def test_accepts_inside_window():
    v, _ = make(now=1_700_000_059.0)
    v.verify(*signed())


def test_rejects_replay_and_forgets_later():
    v, t = make()
    v.verify(*signed())
    with pytest.raises(AuthError, match="replay"):
        v.verify(*signed())
    t["now"] += 200  # the timestamp check now rejects it anyway
    with pytest.raises(AuthError, match="window"):
        v.verify(*signed())
    # the next valid request prunes the expired entry, so the cache stays small
    v.verify(*signed(ts=str(int(t["now"]))))
    assert len(v._seen) == 1


@pytest.mark.parametrize("ts,sig", [(None, "abc"), ("1700000000", None), ("notanumber", "abc")])
def test_rejects_missing_or_bad_headers(ts, sig):
    v, _ = make()
    with pytest.raises(AuthError):
        v.verify("GET", "/jobs/1", ts, sig, b"")


def test_magic_packet():
    pkt = magic_packet("aa:bb:cc:dd:ee:ff")
    assert len(pkt) == 102
    assert pkt[:6] == b"\xff" * 6
    assert pkt[6:12] == bytes.fromhex("aabbccddeeff")
    assert pkt[-6:] == bytes.fromhex("aabbccddeeff")
