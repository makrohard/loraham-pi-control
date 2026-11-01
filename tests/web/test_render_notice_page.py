"""B3-D18a: the console page's re-render notice for a marker that cannot be read, through the public page only (no
import of the CO-14 module, so the case also runs where CO-14 is absent and shows the banner's absence there)."""

from lhpc.core.paths import Paths

NOTICE = 'id="ws-render-notice">Apply needed: the update has not re-rendered the stack proxies yet'


def test_a_render_marker_that_cannot_be_read_shows_the_generic_banner(tmp_path, web):
    paths = Paths(runtime_root=tmp_path)
    (tmp_path / "state" / "webserver-render.pending").mkdir(parents=True)   # not a regular file: unreadable
    page = web(paths=paths).get("/").get_data(as_text=True)
    assert NOTICE in " ".join(page.split())
