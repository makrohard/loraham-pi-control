"""A15: the daemon Settings' repository section is headed "Repositories" (the per-band wording "Repository & RadioLib
(applies to both bands)" is gone); the form under it keeps its fields."""
from htmlq import parse


def _settings(client):
    doc = parse(client.get("/stacks?radio=r1&cfg=daemon").get_data(as_text=True))
    return doc.within(doc.by_id("stack-settings-daemon"))


def test_the_daemon_repository_section_is_headed_repositories(web):
    s = _settings(web())
    summaries = [x.text for x in s.find("summary")]
    assert "Repositories" in summaries and not any("both bands" in t for t in summaries), summaries
    form = next(f for f in s.find("form") if f["action"] == "/stacks/daemon/config")
    names = {i["name"] for i in s.within(form).find("input")}
    assert names == {"_csrf", "remote_libch341", "remote_loraham-daemon", "remote_radiolib"}, names
