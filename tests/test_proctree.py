

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
