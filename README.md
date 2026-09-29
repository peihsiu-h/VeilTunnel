<p align="center"><img src="assets/veiltunnel.png" width="96" alt="VeilTunnel icon"></p>

<h1 align="center">VeilTunnel</h1>

<p align="center">
A lightweight personal VPN that hides inside ordinary HTTPS.<br>
Server and client both come with a GUI and ship as single-file Windows executables.
</p>

<p align="center"><b>English</b> · <a href="README.zh-TW.md">正體中文</a></p>

---

## What it does

VeilTunnel carries your traffic over a single **WebSocket-over-TLS (`wss://`)** connection.
To anyone watching, the server looks like a plain website: `GET /` returns a harmless
"It works!" page, and only a client that knows the pre-shared key (PSK) can open the
tunnel endpoint.

```
 Browser / apps
      │  Windows system proxy (HTTP)  or  SOCKS5 / HTTP proxy
      ▼
 VeilTunnel Client  (127.0.0.1:1080 — SOCKS5 + HTTP on one port)
      │
      │  wss://your-server/ws/updates   ← one TLS connection, many TCP streams
      ▼
 VeilTunnel Server  (:443)
      ├── "/"            → decoy web page
      └── "/ws/updates"  → tunnel (PSK required) ──► Internet
```

## Features

- **Full tunnel**: every connection that reaches the client goes through the VPN. With
  one checkbox the client points the **Windows system proxy** at itself, so browsers and
  most apps are covered automatically. The original settings are restored on disconnect,
  and on the next launch if the app was killed.
- **Looks like HTTPS**: TLS on port 443, a decoy home page, no FastAPI docs endpoints,
  no `Server:` header.
- **PSK authentication** with time-based HMAC tokens. Optionally, IPs that send a wrong
  key are banned permanently. You can manage the ban list in the server GUI.
- **Multiplexing**: many TCP streams share one WebSocket; outbound connects on the
  server run concurrently.
- **Remote DNS**: host names are resolved on the server (HTTP proxy and SOCKS5h), so
  lookups don't leak locally.
- **Optional rate limit** on the client (Mbps, upload + download combined; 0 = unlimited).
- **Auto self-signed certificate**, or bring your own (e.g. Let's Encrypt).
- **Portable**: settings, certificates, logs and the ban list live next to the `.exe`.
- GUI in **English / 正體中文** (follows the Windows display language).

## Quick start (Windows executables)

Build the executables yourself (see [Build](#build-the-executables)), or download them
from the Releases page if they are published there.

### 1. Server: `VeilTunnel-Server.exe`

Run it on a machine with a public IP (or behind a router with port forwarding).

1. Start `VeilTunnel-Server.exe`. A random PSK is generated for you.
2. Choose the port (default **443**) and keep **TLS** on. Leave certificate and key
   blank to use an auto-generated self-signed certificate.
3. Click **Start**. Allow the program through Windows Firewall when asked.
4. If the server is behind a router, forward the TCP port to this machine.
5. Click **Copy** next to *PSK* and send the key to your client over a safe channel.
   The *Client URL* shows the address format:
   `wss://<your-public-ip-or-domain>:443/ws/updates`.

### 2. Client: `VeilTunnel-Client.exe`

1. Enter the **Server URL** and the **PSK**.
2. With a self-signed server certificate, untick **Verify server TLS certificate**
   (see [Security notes](#security-notes)).
3. Keep **Route all traffic: set the Windows system proxy while connected** ticked.
4. Click **Connect**. The light turns green when the tunnel is up.
5. Check it: open <https://httpbin.org/ip>. It should show the server's IP.

Apps that don't follow the system proxy can be pointed at the local proxy directly:
SOCKS5 or HTTP proxy `127.0.0.1:1080`.

## Run from source

Requires Python 3.10+.

```bash
pip install -r requirements.txt

# GUIs
python veiltunnel_server.py
python veiltunnel_client.py

# Command line (reads the same *_config.json; flags override it)
python server/main.py --psk "<long-random-key>" --port 443
python client/main.py --server wss://your.server:443/ws/updates --psk "<key>" --no-verify
```

Run `python server/main.py -h` or `python client/main.py -h` to see all options.
The PSK can also come from the `VEILTUNNEL_PSK` environment variable.

## Build the executables

```bat
py -m pip install -r requirements-build.txt
build.bat
```

This produces `dist\VeilTunnel-Server.exe` and `dist\VeilTunnel-Client.exe`
(PyInstaller one-file builds with no console window).

## Settings

Each GUI keeps its settings in a JSON file next to the executable, or in the project root
when running from source. Examples:
[`server_config.example.json`](server_config.example.json) and
[`client_config.example.json`](client_config.example.json).

| Server | Default | Meaning |
|---|---|---|
| `host` / `port` | `0.0.0.0` / `443` | Listen address |
| `psk` | random | Pre-shared key, at least 8 characters |
| `tls` | `true` | Serve `wss://`. Turn off only for testing, or behind a TLS reverse proxy |
| `cert` / `key` | blank | Certificate files; blank = auto self-signed (`certs/`) |
| `auto_ban` | `true` | Permanently ban IPs that fail authentication (`banned_ips.txt`) |

| Client | Default | Meaning |
|---|---|---|
| `server` | — | `wss://host:port/ws/updates` |
| `psk` | — | Must match the server |
| `verify_ssl` | `true` | Verify the server certificate |
| `bind` / `port` | `127.0.0.1` / `1080` | Local SOCKS5 + HTTP proxy |
| `rate_limit_mbps` | `0` | Throughput cap; 0 = unlimited |
| `system_proxy` | `true` | Set the Windows system proxy while connected |

Both files also accept `language` (`auto`, `en` or `zh-TW`) and `log_level`.

## Security notes

- **Self-signed certificates**: turning off certificate verification means someone who
  can intercept your connection could pretend to be your server. The PSK stops them from
  using your server, but not from reading traffic they intercept. For stronger security,
  give the server a domain name and a real certificate (e.g. Let's Encrypt) and keep
  verification on.
- **Use a long random PSK** (the *Generate* button does this). Authentication uses
  time-based tokens, so client and server clocks must agree within about ±5 minutes.
- **Auto-ban is strict**: a single wrong PSK, or a badly wrong clock, bans that IP
  permanently. Loopback addresses are never banned automatically. To unban an IP, use
  the *Ban list* tab or edit `banned_ips.txt`.
- Settings files store the PSK in **plain text**. Keep them private; they are
  git-ignored.

## Limitations

- **TCP only.** UDP (games, VoIP, WebRTC media) is not tunnelled. Browsers normally
  don't use QUIC/HTTP3 when a proxy is set, but WebRTC may still send UDP directly and
  expose your real IP. Disable non-proxied WebRTC UDP in the browser if that matters.
- **System proxy scope.** Only programs that follow the Windows proxy setting use it.
  Examples: browsers, most GUI apps, many .NET/Python programs. Services that use WinHTTP
  and programs with their own network stack are not covered. For these, configure
  SOCKS5/HTTP `127.0.0.1:1080` in the app itself.
- Plain-HTTP proxy requests use one request per connection (`Connection: close`).
  HTTPS goes through `CONNECT` and is unaffected.

## Project layout

```
common/    protocol framing, PSK auth, rate limiter, settings, shared GUI helpers
server/    FastAPI app, tunnel/relay, ban list, certificates, runner, GUI, CLI
client/    WebSocket tunnel, mixed SOCKS5/HTTP proxy, Windows system proxy, runner, GUI, CLI
assets/    icon (+ script to regenerate it)
veiltunnel_server.py / veiltunnel_client.py   GUI entry points (PyInstaller targets)
```

**Wire protocol** (binary WebSocket messages): `[cmd:1][stream_id:2][payload]`, where
`cmd` is one of `TCP_CONNECT`, `TCP_DATA`, `TCP_CLOSE`, `CONNECT_OK` or `CONNECT_FAIL`.
Addresses use SOCKS5-style encoding (IPv4 / domain / IPv6). The first text message is
the auth token `HMAC-SHA256(psk, unix_time // 30)`.

## Disclaimer

VeilTunnel is meant for personal use on servers and networks you are allowed to use.
You are responsible for following local laws and the policies of the networks you
connect through. The software is provided "as is", without warranty.

## License

[MIT](LICENSE)
