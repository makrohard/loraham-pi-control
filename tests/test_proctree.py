

def test_audit_capture_refuses_non_session_leader(monkeypatch):
    # AUDIT S3: a captured token must be a self-led session (sid==pgid==pid); a recycled
    # pid that is a mere member of a foreign session must yield None (fail closed).
    from lhpc.core import proctree
    import builtins
    real_open = builtins.open
    # fabricate a /proc/<pid>/stat where session != pid (foreign session member)
    def fake_open(path, *a, **k):
        if isinstance(path, str) and path.endswith("/stat"):
            import io
            # fields after comm: state ppid pgrp session ... starttime(22nd)
            fields = ["S", "1", "999", "999"] + ["0"] * 15 + ["12345"] + ["0"] * 10
            return io.StringIO("4242 (proc) " + " ".join(fields))
        return real_open(path, *a, **k)
    monkeypatch.setattr(builtins, "open", fake_open)
    assert proctree.capture_session_token(4242) is None      # sid 999 != pid 4242 -> refused


def test_reconstruct_token_roundtrips_a_valid_identity():
    from lhpc.core import proctree
    ident = {"pid": 4242, "starttime": 99, "sid": 4242, "pgid": 4242}
    token = proctree.reconstruct_token(ident)
    assert token == proctree.SessionToken(4242, 99, 4242, 4242)
    assert token.complete


def test_reconstruct_token_rejects_malformed_partial_and_nonpositive():
    from lhpc.core import proctree
    base = {"pid": 1, "starttime": 2, "sid": 3, "pgid": 4}
    assert proctree.reconstruct_token(None) is None            # not a dict
    assert proctree.reconstruct_token("nope") is None          # not a dict
    assert proctree.reconstruct_token({}) is None              # missing every key
    assert proctree.reconstruct_token({**base, "pid": None}) is None      # non-int -> TypeError
    assert proctree.reconstruct_token({**base, "starttime": "x"}) is None  # non-numeric -> ValueError
    del_one = dict(base); del del_one["sid"]
    assert proctree.reconstruct_token(del_one) is None         # partial
    # zero/negative fields int() cleanly but are NOT usable tokens (session_ceased rejects them)
    assert proctree.reconstruct_token({**base, "pid": 0}) is None
    assert proctree.reconstruct_token({**base, "sid": -1}) is None
