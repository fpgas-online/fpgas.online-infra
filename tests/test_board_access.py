"""Render tests for the board-access role's templates (style of test_netif.py:
no Ansible run needed, just jinja2 + the few Ansible filters the templates use)."""
import base64
import json
import pathlib

import jinja2
import yaml

TEMPLATES = pathlib.Path(__file__).resolve().parents[1] / "ansible" / "roles" / "board-access" / "templates"


def render(name, **vars):
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(TEMPLATES)),
        trim_blocks=True,
        keep_trailing_newline=True,
        undefined=jinja2.StrictUndefined,
    )
    env.filters["to_json"] = json.dumps
    env.filters["b64encode"] = lambda s: base64.b64encode(s.encode()).decode()
    # Ansible's bool filter: the vhost task passes gw_cert_exists via `vars:`,
    # so at role runtime it arrives as the STRING "True"/"False".
    env.filters["bool"] = lambda v: str(v).strip().lower() in ("true", "yes", "on", "1")
    vars.setdefault("ansible_managed", "Ansible managed (test)")
    return env.get_template(name).render(**vars)


# Representative welland-shaped vars (see ansible/inventory/host_vars/fpgas.online.yml)
GW_VARS = dict(
    gw_site_id="welland",
    gw_site_name="Welland",
    gw_site_location="Welland, South Australia",
    gw_site_timezone="Australia/Adelaide",
    gw_public_base="https://gw.welland.fpgas.online",
    gw_pi_network="10.21",
    gw_daemon_port=8765,
    gw_boards_path="/etc/fpgas-online/tt-boards.yaml",
    gw_redis_url="redis://127.0.0.1:6379/0",
    switches=[
        {"index": 1, "model": "gsm7252ps", "mgmt_host": "10.1.5.23",
         "snmp_rw_community": "sw1-community"},
        {"index": 2, "model": "s3300", "mgmt_host": "10.1.5.11",
         "snmp_rw_community": "sw2-community"},
    ],
)

TT_BOARDS = [
    {"slug": "tt06", "switch": 2, "port": 6, "kind": "asic"},
    {"slug": "tt09", "switch": 2, "port": 9, "kind": "asic", "enabled": False},
    {"slug": "kianv-1", "port": None, "kind": "kianv"},
    {"slug": "fpga-1", "switch": 2, "port": 33, "kind": "fpga", "enabled": True},
]


def test_gw_yaml_welland():
    out = render("gw.yaml.j2", **GW_VARS)
    cfg = yaml.safe_load(out)
    assert cfg["site"] == {"id": "welland", "name": "Welland",
                           "location": "Welland, South Australia",
                           "timezone": "Australia/Adelaide"}
    assert cfg["public_base"] == "https://gw.welland.fpgas.online"
    assert cfg["boards_file"] == "/etc/fpgas-online/tt-boards.yaml"
    assert cfg["legacy_boards"] == []
    assert cfg["daemon_port"] == 8765
    assert [s["index"] for s in cfg["poe"]["switches"]] == [1, 2]
    assert cfg["poe"]["switches"][1]["mgmt_host"] == "10.1.5.11"
    # the default pethPsePortAdminEnable OID is filled in
    assert cfg["poe"]["switches"][0]["poe_oid"] == "1.3.6.1.2.1.105.1.1.1.3.1"


def test_gw_yaml_no_switches_legacy_nos():
    vars = dict(GW_VARS)
    del vars["switches"]
    vars["switch"] = {"nos": [{"port": 3, "model": "Raspberry_Pi_4", "loc": "front 2"},
                              {"port": 5}]}
    cfg = yaml.safe_load(render("gw.yaml.j2", **vars))
    assert cfg["poe"]["switches"] == []
    assert cfg["legacy_boards"] == [
        {"port": 3, "model": "Raspberry_Pi_4", "loc": "front 2"},
        {"port": 5, "model": "", "loc": ""},
    ]


def test_gw_secrets_yaml():
    out = render("gw-secrets.yaml.j2", gw_user="fpgas-gw",
                 gw_tokens=[{"name": "web", "token": "t1"}, {"name": "all", "token": "t2"}],
                 switches=GW_VARS["switches"], pi_pw="hunter2")
    sec = yaml.safe_load(out)
    assert sec["tokens"] == [{"name": "web", "token": "t1"}, {"name": "all", "token": "t2"}]
    assert sec["poe_communities"] == {1: "sw1-community", 2: "sw2-community"}
    assert base64.b64decode(sec["pi_password_b64"]).decode() == "hunter2"


def test_gw_secrets_yaml_ci_shape():
    # CI: switches defined but without vaulted communities; no pi_pw override
    switches = [{k: v for k, v in s.items() if k != "snmp_rw_community"}
                for s in GW_VARS["switches"]]
    out = render("gw-secrets.yaml.j2", gw_user="fpgas-gw",
                 gw_tokens=[{"name": "ci", "token": "ci-test-token"}], switches=switches)
    sec = yaml.safe_load(out)
    assert sec["poe_communities"] == {}
    assert "pi_password_b64" not in sec


VHOST_VARS = dict(
    gw_domain="gw.welland.fpgas.online",
    gw_port=8090,
    gw_wssh_port=8889,
    gw_ws_read_timeout="3600s",
    streaming={"data_root": "/srv/streams"},
)


def test_vhost_http_only():
    # gw_cert_exists as the string "False" -- the shape Ansible's `vars:` delivers
    out = render("vhost.conf.j2", gw_cert_exists="False", **VHOST_VARS)
    assert "listen [::]:80;" in out
    assert "listen 443" not in out
    assert "proxy_pass http://127.0.0.1:8090;" in out
    assert "location /internal/ {" in out and "return 403;" in out
    assert "proxy_pass http://127.0.0.1:8889;" in out
    assert "alias /srv/streams/hls/source/;" in out
    assert "add_header Access-Control-Allow-Origin *;" in out
    assert "include includes/gw.welland.fpgas.online-ws-boards.conf;" in out
    assert "acme-challenge" in out


def test_vhost_https():
    out = render("vhost.conf.j2", gw_cert_exists=True, **VHOST_VARS)
    assert "listen 443 ssl;" in out
    assert "listen [::]:443 ssl;" in out
    assert "ssl_certificate /etc/letsencrypt/live/gw.welland.fpgas.online/fullchain.pem;" in out
    assert "return 301 https://$host$request_uri;" in out
    # WS upgrade on the events endpoint in the https server too
    assert "location = /api/events {" in out


def test_ws_board_locations():
    out = render("ws-board.conf.j2", tt_boards=TT_BOARDS,
                 gw_pi_network="10.21", gw_daemon_port=8765, gw_ws_read_timeout="3600s")
    # live boards only: tt06 (implicit enabled) + fpga-1; not tt09 (disabled), not kianv-1 (no port)
    assert out.count("location = /ws/board/") == 2
    assert "location = /ws/board/tt06/serial {" in out
    assert "proxy_pass http://10.21.2.6:8765/serial;" in out
    assert "proxy_pass http://10.21.2.33:8765/serial;" in out
    assert "tt09" not in out and "kianv-1" not in out


def test_ws_board_no_catalogue():
    out = render("ws-board.conf.j2", tt_boards=[],
                 gw_pi_network="10.21", gw_daemon_port=8765, gw_ws_read_timeout="3600s")
    assert "location" not in out
