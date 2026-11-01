"""W12 (stage L5, B4 commit 4): an old `[radio]` table in config/local.toml and no hardware list shows one banner
on every console page; nothing is converted. Asserted on rendered pages."""
import pytest
import radios_fixture

OLD = "The old [radio] configuration is present and nothing was converted: set up the radios in the hardware list first."
UNREADABLE = "The old configuration cannot be read."


def _notice(page: str) -> str:
    start = page.find('id="old-radio-notice">')
    if start < 0:
        return ""
    body = page[start + len('id="old-radio-notice">'):page.index("</p>", start)]
    return " ".join(body.replace("<code>", "").replace("</code>", "").split())


@pytest.mark.parametrize("local,radios_list,want", [
    ('[radio]\nhardware = "loraham"\n', False, f"{OLD} Run lhpc hardware to set up the radios."),
    ('[radio]\nhardware = "loraham"\n', True, ""),                 # the hardware list exists: no banner
    ('[web]\nport = 8080\n', False, ""),                           # no old [radio] table
    (None, False, ""),                                             # a fresh box
    ('[radio\n', False, f"{UNREADABLE} Run lhpc hardware to set up the radios."),   # unparsable: never "no old config"
])
@pytest.mark.parametrize("url", ["/", "/stacks"])
def test_the_old_radio_config_banner(web, tmp_path, local, radios_list, want, url):
    (tmp_path / "config").mkdir(exist_ok=True)
    if local is not None:
        (tmp_path / "config" / "local.toml").write_text(local)
    if radios_list:
        radios_fixture.write(tmp_path)
    page = web().get(url).get_data(as_text=True)
    assert _notice(page) == want
    assert page.count('id="old-radio-notice"') == (1 if want else 0)
    if want:
        assert not (tmp_path / "config" / "radios.toml").exists()         # nothing converted
