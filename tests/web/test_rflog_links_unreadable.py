"""Ledger L23: the dashboard's daemon RF-log links never turn a logs/ that cannot be listed into "no files". Such a
logs/ is one warning pill naming it, no link; an absent logs/ simply has no files. logs/ is made a regular file, so
listing it fails (NotADirectoryError) for any user, root included."""
import radios_fixture

ERROR = "logs/ cannot be listed (NotADirectoryError)"
DAEMONS = {100: ["loraham_daemon", "--radio", "433", "--id", "r1"],     # the links render while a daemon is installed
           101: ["loraham_daemon", "--radio", "868", "--id", "r2"]}


def _dashboard(web, tmp_path):
    return web(cmdlines=DAEMONS).get("/").get_data(as_text=True)


def test_a_logs_path_that_cannot_be_listed_is_named_not_shown_as_no_files(web, tmp_path):
    radios_fixture.write(tmp_path)                                # r1 on 433, r2 on 868
    (tmp_path / "logs").write_text("not a directory")
    page = _dashboard(web, tmp_path)
    assert page.count(ERROR) == 2, page.count(ERROR)              # one per band's row (433 and 868)


def test_an_absent_logs_directory_has_no_files_and_no_warning(web, tmp_path):
    radios_fixture.write(tmp_path)
    assert not (tmp_path / "logs").exists()
    assert "cannot be listed" not in _dashboard(web, tmp_path)                    # no warning of any error kind


def test_a_readable_logs_directory_links_its_radios_file(web, tmp_path):
    radios_fixture.write(tmp_path)
    (tmp_path / "logs" / "r1").mkdir(parents=True)
    (tmp_path / "logs" / "r1" / "rf-daemon.log").write_text("x\n")
    page = _dashboard(web, tmp_path)
    assert "cannot be listed" not in page and "job=r1/rf-daemon.log" in page.replace("%2F", "/")
