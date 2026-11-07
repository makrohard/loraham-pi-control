"""Week item 15 (field test F3a; his word W225: "set 12 h - no openhop upstream changes"): LHPC owns openHop's
`repeater.security.jwt_expiry_minutes` and sets it to 720.

openHop's dashboard judges its login token's expiry by the BROWSER's clock (its frontend: valid while
`Date.now() < exp * 1000 - 30000`), while the box mints `exp` from its own clock + jwt_expiry_minutes. A box clock an
hour or more behind (offline, before GPS time) minted a token the phone already took for expired, and the right
password returned to the login page. With 12 h, a lag up to 11 h 59.5 min still logs in.
"""
from __future__ import annotations

import pytest

from meshcore_host import openhop_file as ofile
from meshcore_host import repeater as rep
from meshcore_host.config import HostConfig

KEY = "repeater.security.jwt_expiry_minutes"


def _cfg() -> HostConfig:
    return HostConfig(name="Chat 1", allow="127.0.0.1", bind="127.0.0.1", port=5000, key="11" * 32,
                      frequency=869618000, bandwidth=62500, spreading_factor=8, coding_rate=8, txpower=14,
                      preamble=16, mode="chat+repeater", jwt_secret="j" * 64, repeater_state_dir="/rt/state/openhop",
                      use_gps=False, gps_file="", restart_argv=["lhpc", "restart"])


def _security(user):
    return rep.apply_overlay(user, _cfg())["repeater"]["security"]


def test_r1_the_rendered_config_sets_the_token_lifetime_to_720_minutes():
    """RED before: absent (openHop's default, 60)."""
    sec = _security({"repeater": {"security": {"admin_password": "p" * 15}}})
    assert sec["jwt_expiry_minutes"] == 720 and type(sec["jwt_expiry_minutes"]) is int


@pytest.mark.parametrize("theirs", [60, 5, 100000, "720", None])
def test_r2_an_operators_own_value_is_replaced_like_every_owned_key(theirs):
    """RED before: their value stayed."""
    sec = _security({"repeater": {"security": {"admin_password": "p" * 15, "jwt_expiry_minutes": theirs}}})
    assert sec["jwt_expiry_minutes"] == 720


def test_r3_it_is_an_owned_key():
    """RED before. The guards refuse a change of an owned key, and the marker lists it."""
    assert KEY in ofile.OWNED_KEYS


def test_r4_the_other_owned_keys_and_the_users_keys_stay_beside_it():
    sec = _security({"repeater": {"security": {"admin_password": "p" * 15, "jwt_secret": "stale",
                                               "max_clients": 3}}})
    assert sec == {"admin_password": "p" * 15, "jwt_secret": "j" * 64, "max_clients": 3, "jwt_expiry_minutes": 720}


@pytest.mark.parametrize("lag_h,valid", [(0, True), (2, True), (11, True), (12, False)])
def test_r5_the_dashboards_own_expiry_check_accepts_a_box_clock_behind_by_up_to_12_h(lag_h, valid):
    """The frontend's check (`Date.now() >= exp*1e3 - 3e4` is expired) on a token minted at box time T with the
    rendered lifetime, judged at browser time T + lag. RED before for 2 h and 11 h (60 minutes)."""
    minutes = _security({"repeater": {}})["jwt_expiry_minutes"]
    t_box = 1_800_000_000
    exp = t_box + minutes * 60
    browser_now_ms = (t_box + lag_h * 3600) * 1000
    assert (browser_now_ms < exp * 1000 - 30_000) is valid
