# tinytapeout.fpgas.online — design

Date: 2026-08-22
Status: draft, awaiting review
Scope: Welland site (tweed + s3300). ps1 is out of scope.

A Tiny Tapeout–branded front end to the Welland fpgas.online hardware: the
TT ASIC demo boards on s3300 ports 1–10 (TT01–TT10), specialised KianV
boards (TT chips with the QSPI flash/PSRAM PMOD), and the TT FPGA emulation
boards (iCE40UP5K "ASIC simulator" on a demo board). Every board page is
**camera + Tiny Tapeout Commander**, where Commander is a fork of the
upstream app that reaches the board over the web instead of local
WebSerial/WebUSB.

This document is the cross-repo design. It lives in `fpgas.online-infra`
(like the VLAN-per-port spec) because the infra repo is where the board map,
deployment and verification are tied together; the code lands in the repos
named in §8.

## 1. Goals

1. `https://tinytapeout.fpgas.online/` lists the TT boards at Welland in
   three sections — **ASIC boards TT01–TT10**, **KianV boards**, **FPGA
   emulation boards** — and each board has a page with a live camera, an
   embedded Commander connected to that board, details about the board, and
   links to the published Tiny Tapeout documentation.
2. Look and feel is Tiny Tapeout's (official logo and palette, TT-style nav),
   clearly labelled as a community instance run by fpgas.online at Welland,
   not operated by Tiny Tapeout.
3. A fork of `TinyTapeout/tt-commander-app` whose only hardware dependency is
   a byte-stream transport, so it can drive a board over a WebSocket; the
   fork stays rebaseable on upstream.
4. FPGA emulation boards ship with a curated set of example designs and let a
   visitor load their own design. Phase 1: upload a precompiled iCE40UP5K
   bitstream. Phase 2: upload Verilog and have the **Pi** compile it.
5. KianV boards: one-click boot-to-Linux demo with the Linux console shown in
   the Commander terminal, via a UART bridge running on the demo board's
   RP2040.

## 2. Constraints and decisions (from brainstorming)

| Decision | Choice |
|---|---|
| Topology | One PoE Pi per s3300 port (existing pattern). The TT demo board is on the Pi's USB (RP2040/RP2350 USB-CDC, MicroPython SDK). The Pi also carries a PMOD HAT wired to the TT board's PMOD headers (not used in phases 1–3; reserved). |
| Board set | Ports 1–10 = TT01–TT10 slots. Only TT04+ boards are real today; pre-TT04 / empty slots render as "coming soon". |
| Access model | Fully open, no accounts, no locking (same as fpgas.online). Multiple viewers share one byte stream. |
| KianV console | RP2040 UART bridge into the Commander terminal (not the Pi's UART, not camera-only). |
| Own designs on FPGA boards | **Nothing but the web UI runs on tweed** — no compiles, no serial code. Phase 1 bitstream upload; phase 2 Verilog compile **on the Pi**. |
| FPGA demos | Curated set in a new `fpgas-online/tinytapeout-fpga-demos` repo, built in CI, delivered as a deb. |
| Port map | Data-driven (YAML in infra host_vars → Django rows); KianV/FPGA board ports assigned later. |
| Branding | Official TT logo + palette, "hosted by fpgas.online" disclaimer. Heads-up to the TT team before launch. |
| Pi image | Everything Pi-side is baked into the shared read-only NFS root. **Nothing is installed or fetched after boot.** |
| Serial ownership | Exactly one owner of the serial port per Pi (the bridge); every other consumer — viewers *and* internal tasks — is a bridge client. No arbitration. |
| Process | Right repo per component; feature branches in git worktrees; every change via PR; CI green at every stage. |

## 3. System overview

```
browser ──https──▶ tweed (nginx, vhost tinytapeout.fpgas.online)
                    ├─ /, /board/<slug>/, /docs/      Django `ttsite` (gunicorn)
                    ├─ /static/tt-commander/<ver>/    built Commander embed bundle
                    ├─ /live/<pi-hostname>.m3u8       existing HLS camera (unchanged)
                    ├─ /ws/pistat/<pi>/               existing daphne status socket (unchanged)
                    ├─ /ws/board/<slug>/serial  ─ws─▶ 10.21.<s>.<p>:8765/serial   fpgas-tt daemon on the Pi
                    └─ /api/board/<slug>/…     ─http▶ 10.21.<s>.<p>:8765/…            │
                                                                                       ├─ /dev/ttboard → RP2040 (MicroPython SDK) → TT chip / iCE40
                  snmp toggle (existing) ──▶ s3300 PoE ──▶ Pi power                    └─ (later) PMOD HAT GPIO, Verilog build
```

- **Addressing** follows the VLAN-per-port scheme: switch `s`, port `p` ⇒ Pi
  `pi-sw<s>-p<p>` at `10.21.s.p`. Nothing in this design stores an IP; it is
  derived from `(switch, port)`. The `10.21.0.100+port` convention of the old
  `pibfpgas` app is not used here.
- **Camera** stream names are the Pi hostname (`fpgas-online-cam` publishes
  `rtmp://<gw>/pib/$(hostname --short)`), so a board's stream is
  `/live/pi-sw<s>-p<p>.m3u8`.
- **tweed** runs nginx + Django only. The WebSocket path is an nginx proxy
  (no Python in the data path); the HTTP API paths are thin Django proxies
  (slug → IP lookup, size limits, CSRF) to the Pi daemon.
- **Pi** runs the `fpgas-tt` daemon (from the `fpgas-online-tt` deb, baked
  into the NFS root) which owns the serial port and exposes the bridge and
  tasks over HTTP/WS on `:8765`, reachable only from tweed.

## 4. Component A — Commander fork (`fpgas-online/tt-commander-app`)

GitHub fork of `TinyTapeout/tt-commander-app`. `main` tracks upstream
untouched; work lands on branch `fpgas-online` via PRs into that branch.
Every change is additive behind a transport abstraction so it can be offered
upstream.

### A.1 Transport abstraction

- `src/transport/SerialTransport.ts`:
  ```ts
  export interface SerialTransport extends EventTarget {
    readonly readable: ReadableStream<Uint8Array>;
    readonly writable: WritableStream<Uint8Array>;
    readonly state: 'connecting' | 'open' | 'closed';
    close(): Promise<void>;
    // events: 'open', 'close', 'error'
  }
  ```
  This is exactly the subset of `SerialPort` that `TTBoardDevice` uses.
- `WebSerialTransport(port: SerialPort)` — existing behaviour.
- `WebSocketTransport(url)` — binary WebSocket; frames → `readable`,
  `writable` chunks → `ws.send`; reconnect with exponential back-off
  (1 s → 30 s) surfacing `state`; text frames are out-of-band events from the
  daemon (`{"event": "..."}`) and are dispatched as `CustomEvent('message')`,
  never written into `readable`.
- `TTBoardDevice` takes a `SerialTransport`. Its REPL bootstrap (Ctrl-C ×2,
  Ctrl-B, Ctrl-A, upload `ttcontrol.py`, `read_rom()`, `dump_state()`) is
  unchanged — the MicroPython side cannot tell the difference.

### A.2 Embeddable build

- `src/embed.tsx` exports
  `mountCommander(el: HTMLElement, opts: EmbedOptions)` with
  ```ts
  interface EmbedOptions {
    transport: { kind: 'websocket'; url: string } | { kind: 'webserial' };
    board: { slug: string; kind: 'asic' | 'kianv' | 'fpga'; shuttle?: string };
    apiBase?: string;          // e.g. "/api/board/<slug>"; required for kind=fpga|kianv
    chrome?: { header: boolean; footer: boolean };  // default false/false when embedded
    admin?: boolean;           // shows "Reset to Bootloader"; default false
  }
  ```
- Vite builds two targets: the standalone app (unchanged, WebSerial) and a
  library bundle `dist/embed/tt-commander-embed.{js,css}` (ES module, all
  deps inlined, no external fonts). Release tag → GitHub Release asset
  `tt-commander-embed-<ver>.tar.gz`.
- Embedded mode skips the WebSerial support check and the "Connect" button,
  connects on mount, and replaces "Disconnect" with "Reconnect".
  "Reset to Bootloader" is hidden unless `admin` (a remote board in BOOTSEL
  mode is dead until someone local copies a UF2). The firmware-upgrade block
  becomes an informational banner.

### A.3 Kind-aware behaviour

- `asic`: upstream behaviour; project list from `index.tinytapeout.com`.
- `kianv`: a "Boot Linux" button → `POST ${apiBase}/kianv/boot` (the sequence
  lives in the daemon, §5.4) then switches to the terminal tab, titled
  "Linux console".
- `fpga`: the project list comes from `GET ${apiBase}/designs` (the board's
  `/bitstreams/*.bin` merged with demo metadata) instead of the TT index; the
  Config tab gains a "Load design" action (`POST ${apiBase}/designs/<name>/enable`)
  and the PinoutPanel renders the demo's `pinout` from that metadata.

### A.4 Multi-viewer

All viewers receive the same bytes (the daemon fans out); the app already
tolerates unsolicited `tt.*=` lines (that is how monitoring works), so state
converges. Nothing further is engineered; the page says "other people may be
driving this board too".

### A.5 CI

GitHub Actions: eslint, prettier check, `vitest` (new tests:
`WebSocketTransport` against an in-process mock WS server, `embed` smoke
mount), `vite build` both targets; release workflow on tag.

## 5. Component B — Pi daemon (`fpgas-online/fpgas.online-tt` → deb `fpgas-online-tt`)

### 5.1 Packaging and runtime

- Python ≥ 3.11, `aiohttp` (HTTP + WS), `pyserial-asyncio`, `pyyaml`.
  nfpm deb, `arch: all`, published to `fpgas-online/apt` by the repo's
  release workflow (same shape as `fpgas-online-cam`/`fpgas-setup-pi`).
- Installs: `/usr/lib/python3/dist-packages/fpgas_tt/`, `/usr/bin/fpgas-tt`,
  `/usr/lib/systemd/system/fpgas-tt.service` (enabled by the Ansible Pi play;
  it does not depend on the device unit — it starts at boot and retries
  opening the serial device every 1 s until a board appears), udev rule
  `/etc/udev/rules.d/60-fpgas-tt.rules` (`2e8a:0005` and `2e8a:000f` →
  `SYMLINK+="ttboard"`, `GROUP="dialout"`), runs as user `pi`.
- Configuration is **discovered, not generated**: at start the daemon reads
  `hostname --short` → `(switch, port)` and looks itself up in the baked
  `/etc/fpgas-online/tt-boards.yaml` (the same file tweed uses, §7.1) to get
  `slug` and `kind`. Unknown hostname ⇒ plain `asic` bridge mode. No
  per-Pi files, no boot-time fetches.

### 5.2 The bridge — single owner

One `Bridge` object owns `/dev/ttboard`: one reader task fanning every chunk
out to all registered clients, one writer queue. **Every** consumer is a
client:

- WebSocket clients (`/serial`): binary frames ↔ bytes. A client whose send
  queue exceeds 256 KiB is dropped (never the reader). On serial loss all
  clients are closed with code 1011 and reason `board disconnected`; the
  daemon re-opens the device every 1 s and announces `{"event":"board","present":true}`
  to new clients.
- Internal task clients (§5.3): write the raw-REPL byte sequence into the same
  writer queue and parse their answer out of the same fan-out stream, framed
  by MicroPython's raw-REPL `\x04>`…`OK`…`\x04\x04>` markers plus a per-task
  nonce they print first. No locking: if a human types during a task the
  task sees a malformed reply, fails with the captured bytes in the error,
  and the user retries. Viewers see task traffic in their debug log — by
  design.

### 5.3 HTTP API (all JSON unless noted; listened on `0.0.0.0:8765`)

| Method/path | Kinds | Behaviour |
|---|---|---|
| `GET /health` | all | `{board:{present,device,vid_pid}, kind, slug, clients, uptime_s, version}` |
| `WS /serial` | all | the bridge |
| `GET /designs` | fpga | Raw-REPL `os.listdir('/bitstreams')` merged with `/usr/share/fpgas-tt/demos/index.json` → `[{name,title,author,description,docs_url,repo_url,clock_hz,pinout,source:'demo'|'upload'}]` |
| `POST /designs/<name>/enable` | fpga | body `{clock_hz?}` → `tt.shuttle[<name>].enable()` (+ `set_clock_hz`) |
| `POST /bitstream` | fpga | multipart `file`, `name`; checks: ≤ 256 KiB, iCE40 preamble `7E AA 99 7E` present in the first 64 bytes, `name =~ ^[a-z0-9_]{1,40}$`, not a demo name; writes `/bitstreams/<name>.bin` via raw REPL in 256-byte base64 chunks (the `tt_fpga.py`/`TTDBConfig.write_file` protocol), verifies size with `os.stat`; uploads are capped at 16 files — oldest `source:'upload'` evicted |
| `POST /kianv/boot` | kianv | the boot macro (§5.4) |
| `POST /demos/sync` | fpga | copy any `/usr/share/fpgas-tt/demos/*.bin` missing on the board; the daemon also runs this itself once the board is first opened after service start |
| (reserved) `POST /build` | fpga | phase 2: Verilog → bitstream on the Pi |

Errors are `{error: "<human message>", detail: "<captured REPL output>"}` with
4xx/5xx; nothing is swallowed.

### 5.4 KianV boot macro (phase 3; includes a spike)

Target: select the KianV design at its clock (30 MHz for tt06 `tt_um_kianV_rv32ima_uLinux_SoC`),
then bridge the chip's UART (`uo_out[4]` TX, `ui_in[3]` RX; 3.5 Mbaud at
30 MHz per the project docs) to the USB-CDC stream so the Commander terminal
is the Linux console. The bridge runs on the RP2040 (hardware `machine.UART`
if the GPIO mapping for those pins lands on a UART function, else an `rp2.PIO`
UART; lowering the project clock lowers the baud proportionally if 3.5 Mbaud
is not achievable). **Spike first**: prove the bridge on a real board with
`mpremote`, then encode the exact sequence in the daemon as `kianv_boot.py`
pushed through the raw REPL, and add a regression test against the fake REPL.

### 5.5 Tests

`pytest` + `pytest-aiohttp`: a fake MicroPython raw-REPL on a pty (echo of
Ctrl-A/Ctrl-D framing, `os.listdir`, file writes) exercises the bridge
(fan-out, slow-client drop, reconnect), every task end-to-end, validation
failures, and the "human typed during task" failure path. Lint: ruff. deb
build in CI.

## 6. Component C — Django `ttsite` app (in `fpgas-online/fpgas.online-site`)

### 6.1 Models

```python
class Board(models.Model):
    KIND = [("asic", "TT ASIC"), ("kianv", "KianV RISC-V"), ("fpga", "FPGA emulation")]
    slug = SlugField(unique=True)
    switch = PositiveSmallIntegerField(default=1)
    port = PositiveSmallIntegerField(null=True, blank=True)   # null => "coming soon"
    kind = CharField(choices=KIND)
    shuttle = CharField(blank=True)            # "tt06"; blank for fpga
    title = CharField()                        # "Tiny Tapeout 6"
    blurb = CharField()                        # one line under the tile
    description = TextField(blank=True)        # markdown: what's on this board
    pcb = CharField(blank=True)                # "TT demo board v3.x (RP2040)"
    pmods = JSONField(default=list)            # [{name, url, note}]
    links = JSONField(default=list)            # [{label, url}]
    enabled = BooleanField(default=True)
    sort_order = PositiveSmallIntegerField(default=0)

    @property hostname -> f"pi-sw{switch}-p{port}"
    @property ip       -> f"10.21.{switch}.{port}"
    @property stream_url -> f"/live/{hostname}.m3u8"
    @property serial_ws_path -> f"/ws/board/{slug}/serial"
    @property api_base -> f"/api/board/{slug}"
    @property live -> port is not None and enabled
```

Demos/designs are not modelled; they are read live from the daemon.

### 6.2 Seeding

`manage.py ttsite_loadboards /etc/fpgas-online/tt-boards.yaml` — idempotent
upsert by slug, deletes rows whose slug vanished only with `--prune`. Admin
registered for ad-hoc edits.

### 6.3 Host routing

A 20-line middleware: if `request.get_host()` is the configured
`TTSITE_HOST` (`tinytapeout.fpgas.online`), set `request.urlconf =
"ttsite.urls"`. No `django-hosts`. `ALLOWED_HOSTS`/`CSRF_TRUSTED_ORIGINS`
gain the new host (Ansible `local_settings.py` lines).

### 6.4 URLs and views (`ttsite.urls`)

| URL | View |
|---|---|
| `/` | Landing: hero + three sections; ASIC section is a fixed 10-slot grid for TT01–TT10 (live thumbnail, shuttle link, "Use this board" / "coming soon"); KianV and FPGA sections list their boards; "How this works"; doc links. |
| `/board/<slug>/` | Board page (§6.5). |
| `/board/<slug>/status.json` | Proxies daemon `/health`, 5 s cache, `{present, reachable, clients}` for the status pill. |
| `/api/board/<slug>/designs`, `…/designs/<name>/enable`, `…/bitstream`, `…/kianv/boot` | Thin proxies to `http://<ip>:8765/…` with a 30 s timeout (bitstream: streamed body, `client_max_body_size 1m` in nginx, Django `DATA_UPLOAD_MAX_MEMORY_SIZE` respected), CSRF-protected POSTs, daemon error JSON passed through with its status. |
| `/docs/` | Curated TT documentation index (get started, demo board guide, specs/pinouts/PMODs, SDK + firmware, FPGA breakout guide, Commander upstream, each shuttle's chip page, KianV project pages). |

The WebSocket path `/ws/board/<slug>/serial` is **not** a Django route; nginx
proxies it straight to the Pi (§7.2).

### 6.5 Board page composition

Two columns (camera 40 % | Commander 60 %), stacking on narrow screens:

1. Header strip: title, kind badge, shuttle link, status pill (board present
   / Pi unreachable / coming soon), "Power-cycle board" (existing
   `/snmp/toggle` with the board's port) and "Reset video".
2. Camera: `video.js` HLS from `stream_url` (existing include).
3. Commander: `<div id="tt-commander" data-…>` + `mountCommander(...)` with
   the board's transport URL, kind, shuttle, `apiBase`.
4. Board info card: `description`, `pcb`, fitted `pmods`, `links`, plus
   auto-links: `https://tinytapeout.com/chips/<shuttle>/`,
   the demo board guide, SDK docs, pinout specs.
5. Kind extras: `kianv` → "Boot Linux" explanation + QSPI PMOD note;
   `fpga` → demo gallery (cards from `/designs` with Run buttons) and
   "Upload your own bitstream" form (`.bin`, name, link to the
   `tt_fpga.py harden` instructions and the demo repo as a template).
6. Status log: existing `pistat` socket (`dcws.js`, refactored so the DOM ids
   it needs are parameters, backwards compatible with `fpga.html`).

### 6.6 Theme

`ttsite/templates/ttsite/base.html`: TT logo (from the TT website repo,
licence noted in `static/ttsite/README`), palette primary `#544ead` /
secondary `#8afbfd`, Roboto, top nav (Boards · ASIC · KianV · FPGA · Docs ·
tinytapeout.com ↗), footer: "A community-run Tiny Tapeout hardware instance
hosted by fpgas.online at Welland, South Australia — not operated by Tiny
Tapeout Ltd. Source on GitHub." Responsive, no CDN JS beyond what the
existing site already uses (video.js).

### 6.7 Tests

`pytest-django` job added to the site's CI: model properties, loader
command (fresh + idempotent + `--prune`), host middleware, proxy views
against a mocked daemon, template smoke per kind (coming-soon, asic, kianv,
fpga). ruff stays blocking.

## 7. Component D — infra (`fpgas-online/fpgas.online-infra`)

### 7.1 Source of truth: `tt_boards`

`host_vars/fpgas.online.yml`:
```yaml
tt_boards:
  - {slug: tt01, port: 1, kind: asic, shuttle: tt01, title: "Tiny Tapeout 1", enabled: false}
  - ...
  - {slug: tt06, port: 6, kind: asic, shuttle: tt06, title: "Tiny Tapeout 6",
     pcb: "TT demo board v3 (RP2040)", blurb: "…"}
  - ...
  - {slug: tt10, port: 10, kind: asic, shuttle: tt10, title: "Tiny Tapeout 10"}
  - {slug: kianv-1, port: null, kind: kianv, shuttle: tt06, title: "KianV uLinux SoC (TT06)",
     pmods: [{name: "QSPI Pmod", url: "https://github.com/mole99/qspi-pmod"}]}
  - {slug: fpga-1, port: null, kind: fpga, title: "TT FPGA emulation board 1"}
  ...
```
`switch` defaults to 1. A new `ttsite` role renders this list to
`/etc/fpgas-online/tt-boards.yaml` on tweed **and** into the Pi NFS root
(same template, so daemon and site agree), then runs `ttsite_loadboards`.

### 7.2 Server (`ttsite` role + existing roles)

- nginx vhost `tinytapeout.fpgas.online`: static, `/live`, `/ws/pistat`,
  gunicorn proxy, and one generated `location = /ws/board/<slug>/serial`
  per *live* board → `proxy_pass http://10.21.<s>.<p>:8765/serial` with
  upgrade headers, `proxy_read_timeout 3600s`; `/api/` → gunicorn with
  `client_max_body_size 1m`.
- certbot: add the name to the existing certificate request (SAN).
- Commander embed: download the tagged release tarball into
  `{{ static_dir }}/tt-commander/<ver>/` (checksum pinned in vars); no node on
  tweed.
- `firewall` role: allow tweed → `v*` interfaces tcp/8765 (new input/forward
  rule class next to ssh/rtmp; Pis still cannot reach each other).
- DNS: `tinytapeout.fpgas.online` A/AAAA → tweed, in the PowerDNS zone data
  managed by `mithro/hetzner-ansible` (`fpgas.hosts`). Out of band for this
  repo; listed in the runbook.
- `verify-server.yml`: vhost answers 200, WS location present per live
  board, embed bundle present, firewall rule present.

### 7.3 Pi NFS root (Pi play)

- `apt install fpgas-online-tt fpgas-online-tt-demos` inside the nspawn
  chroot (via the fpgas-online apt repo, like `fpgas-online-cam`).
- `/etc/fpgas-online/tt-boards.yaml` baked from the same template.
- `systemctl enable fpgas-tt.service`.
- `verify-pi.yml`: service active; `/health` answers; when `/dev/ttboard`
  exists, `board.present == true`.
- Arch note: `srv.yml` still builds the root from the **armhf** bookworm
  image while `fpgas-setup-pi`'s nfpm says `arm64` — both new debs are
  `arch: all` so they are unaffected; the mismatch is raised as a separate
  issue. Phase 2 (Verilog on the Pi) needs an **arm64** root because
  oss-cad-suite only ships `linux-arm64`.

### 7.4 VM test

The QEMU harness cannot emulate the RP2040. Coverage there is: deb installed,
service active, `/health` returns `board.present=false`, nginx vhost renders.
Hardware-in-the-loop checks run in `verify-pi.yml` against production.

## 8. Component E — demo bundle (`fpgas-online/tinytapeout-fpga-demos` → deb `fpgas-online-tt-demos`)

- One TT-template project per directory (`info.yaml`, `src/`, `docs/info.md`,
  optional cocotb `test/`), each a valid starting point for a real TT
  submission: `tt_um_factory_test`, `tt_um_counter_7seg`, `tt_um_pwm_breathe`,
  `tt_um_uart_hello`, `tt_um_vga_pattern`, `tt_um_wokwi_example`; stretch:
  `tt_um_kianv_fpga` if KianV fits the UP5K.
- CI: oss-cad-suite on x86 → `tt_fpga.py harden` per demo → cocotb where
  present → `bundle/index.json` + `.bin` → nfpm deb `fpgas-online-tt-demos`
  (`arch: all`, installs to `/usr/share/fpgas-tt/demos/`) → on tag, publish
  to `fpgas-online/apt`. Apache-2.0.
- Updating demos in production = bump the deb, re-run the Pi play (rebuilds
  the NFS root), reboot Pis. No runtime fetch.

## 9. Error handling

| Condition | Behaviour |
|---|---|
| Pi unreachable | status pill grey "Pi offline", Commander shows the transport state, "Power-cycle board" offered; `/status.json` says `reachable:false`. |
| Board absent on the Pi | pill red "board not detected", daemon message verbatim. |
| WS drops | transport reconnects with back-off and shows state; REPL re-bootstraps on open. |
| Upload rejected | daemon's `error` + `detail` shown under the form. |
| Task corrupted by concurrent typing | task fails with captured output; UI says "retry — someone else may be typing". |
| Daemon crash | systemd `Restart=always`, `RestartSec=1`. |
| Port null / disabled | page renders "coming soon" with docs; no WS/API routes generated. |

## 10. Testing summary

| Repo | Tests | CI gate |
|---|---|---|
| tt-commander-app (fork) | vitest: transport, embed mount; eslint/prettier; both builds | PR to `fpgas-online` |
| fpgas.online-tt | pytest vs fake raw-REPL pty: bridge, tasks, validation, failure paths; ruff; deb build | PR to `main` |
| fpgas.online-site | pytest-django: models, loader, middleware, proxies, templates; ruff | PR to `main` |
| tinytapeout-fpga-demos | cocotb + full build of every demo; deb build | PR to `main` |
| fpgas.online-infra | yamllint, ansible-lint, VM test; `verify-*.yml` | PR to `main` |

## 11. Phasing

1. **Core** — fork transport + embed; daemon bridge + `/health`; `ttsite`
   landing + ASIC board pages (TT04–TT10) + docs page + theme; infra vhost,
   firewall, NFS-root deb, verify. Ships the ASIC section end-to-end.
2. **FPGA** — demo repo + deb; daemon `/designs`, `/bitstream`, `/enable`,
   demo sync; fork `fpga` kind; site gallery + upload form.
3. **KianV** — UART-bridge spike on hardware; daemon `/kianv/boot`; fork
   `kianv` kind; site extras.
4. **Later** — Verilog build on the Pi (`/build`, needs arm64 root +
   oss-cad-suite); PMOD-HAT features (pin driving / logic capture from the
   Pi); contributing the transport abstraction upstream.

Each phase gets its own implementation plan (writing-plans) and its own PR
series; a phase is done when its CI is green and its verify playbooks pass
against Welland.

## 12. Process

- Work in the component's own repo; feature branches in git worktrees
  (`.worktrees/` per repo, gitignored); every change via PR; CI green at
  every stage; no direct pushes to `main`.
- Small, discrete commits; Apache-2.0 for everything new; ISO dates.

## 13. Open items

1. KianV UART bridge on the RP2040 (hardware UART pin function vs PIO UART;
   achievable baud) — spike in phase 3.
2. Which TT demo-board revisions / firmware versions are on each port — filled
   into `tt_boards` at wiring time.
3. Heads-up to the Tiny Tapeout team about using the logo and hosting a
   public Commander instance; before public launch.
4. armhf vs arm64 Pi root (affects phase 4 only).
