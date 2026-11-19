"""A15: the daemon's repository section is headed "Repositories" (the per-band wording "Repository & RadioLib
(applies to both bands)" is gone); the form under it keeps its fields. UI-REORG c3: it is Install › Repositories (was a
Settings child holding the whole config form); its own form posts the remotes only."""
from htmlq import parse


def _install(client):
    doc = parse(client.get("/stacks?radio=r1&cfg=daemon").get_data(as_text=True))
    assert "Repositories" not in [x.text for x in doc.within(doc.by_id("stack-settings-daemon")).find("summary")]
    return doc.within(doc.by_id("stack-install-daemon"))


def test_the_daemon_repository_section_is_headed_repositories(web):
    s = _install(web())
    summaries = [x.text for x in s.find("summary")]
    assert "Repositories" in summaries and not any("both bands" in t for t in summaries), summaries
    form = next(f for f in s.find("form") if f["action"] == "/stacks/daemon/remotes")
    names = {i["name"] for i in s.within(form).find("input")}
    assert names == {"_csrf", "remote_libch341", "remote_loraham-daemon", "remote_radiolib"}, names
