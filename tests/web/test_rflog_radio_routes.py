"""0.14.0 (REL14-D33): a radio's daemon RF log `r<rid>/rf-daemon.log` (W7-8a) opens through the web routes: the
page shows the file with its radio and stack rows (W4), and the records API serves it. Any other path stays refused.
Found by the release run's browser lane (the page dropped the job and rendered empty)."""
import pytest
import radios_fixture

LINE = "2026-09-12T16:01:00.000Z TX rssi=- snr=- len=3 outcome=ok hex=112233"


def _seed(tmp_path):
    radios_fixture.write(tmp_path)                        # r1 on 433, r2 on 868
    (tmp_path / "logs" / "r2").mkdir(parents=True)
    (tmp_path / "logs" / "r2" / "rf-daemon.log").write_text(LINE + "\n")


def test_a_radios_daemon_log_page_shows_the_file_and_its_radio_row(web, tmp_path):
    _seed(tmp_path)
    page = web().get("/logs/loraham-daemon?job=r2/rf-daemon.log").get_data(as_text=True)
    assert 'data-job="r2/rf-daemon.log"' in page
    assert str(tmp_path / "logs" / "r2" / "rf-daemon.log") in page
    assert 'class="rfbands"' in page
    radio_row = page[page.index('class="rfbands"'):page.index('class="rfswitch"')]
    assert radio_row.count('aria-current="page"') == 1 and '>r2 · 868</a>' in radio_row.split('aria-current="page"')[1][:20]


def test_a_radios_daemon_log_records_come_from_its_file(web, tmp_path):
    _seed(tmp_path)
    r = web().get("/api/rflog/loraham-daemon?job=r2/rf-daemon.log")
    assert r.status_code == 200
    data = r.get_json()
    assert (data["job"], data["path"]) == ("r2/rf-daemon.log", str(tmp_path / "logs" / "r2" / "rf-daemon.log"))
    assert len(data["records"]) == 1


@pytest.mark.parametrize("job", ["r2/../x", "r100/rf-daemon.log", "r1/other.log", "r0/rf-daemon.log",
                                 "../r2/rf-daemon.log", "r2/rf-daemon.log/x", "/r2/rf-daemon.log", "R2/rf-daemon.log",
                                 "r2//rf-daemon.log"])
def test_any_other_path_is_refused(web, tmp_path, job):
    _seed(tmp_path)
    c = web()
    assert c.get(f"/api/rflog/loraham-daemon?job={job}").status_code == 404
    page = c.get(f"/logs/loraham-daemon?job={job}").get_data(as_text=True)
    assert 'data-job=""' in page and 'class="rfbands"' not in page
