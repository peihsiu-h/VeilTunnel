<p align="center"><img src="assets/veiltunnel.png" width="96" alt="VeilTunnel icon"></p>

<h1 align="center">VeilTunnel</h1>

<p align="center">
藏在一般 HTTPS 流量裡的輕量個人 VPN。<br>
Server 與 Client 都有 GUI，並可打包成 Windows 單一執行檔。
</p>

<p align="center"><a href="README.md">English</a> · <b>正體中文</b></p>

---

## 這是什麼

VeilTunnel 把你的流量放進**一條 WebSocket over TLS（`wss://`）連線**裡傳送。
從外部看，Server 就是一個普通網站：`GET /` 會回一個「It works!」的預設頁面，
只有知道預共用金鑰（PSK）的 Client 才能打開隧道端點。

```
 瀏覽器 / 各種程式
      │  Windows 系統 Proxy（HTTP），或 SOCKS5 / HTTP Proxy
      ▼
 VeilTunnel Client（127.0.0.1:1080，同一個 port 同時支援 SOCKS5 + HTTP）
      │
      │  wss://your-server/ws/updates   ← 一條 TLS 連線承載多條 TCP stream
      ▼
 VeilTunnel Server（:443）
      ├── "/"            → 偽裝網頁
      └── "/ws/updates"  → 隧道（需 PSK）──► 網際網路
```

## 功能

- **全流量走 VPN**：進到 Client 的每條連線都經過 VPN。勾選一個選項，Client 就會把
  **Windows 系統 Proxy** 指向自己，瀏覽器和大多數程式不用個別設定就會走 VPN。
  中斷連線時會還原原本的設定；程式被強制結束的話，下次啟動時會自動還原。
- **偽裝成 HTTPS**：使用 443 port 的 TLS、有偽裝首頁、不開放 FastAPI 文件頁、
  不送 `Server:` header。
- **PSK 驗證**：使用以時間為基礎的 HMAC token。可選擇把 PSK 錯誤的 IP 永久封鎖，
  封鎖名單可以在 Server GUI 裡管理。
- **多工**：多條 TCP stream 共用一條 WebSocket；Server 端的對外連線是並行建立的。
- **遠端 DNS**：網域名稱在 Server 端解析（HTTP proxy 與 SOCKS5h），DNS 查詢不會從本機洩漏。
- **可選的速率限制**（Client 端，單位 Mbps，上下行合計；0 = 不限速）。
- **自動產生自簽憑證**，也可以改用自己的憑證（例如 Let's Encrypt）。
- **免安裝**：設定檔、憑證、log、封鎖名單都放在 `.exe` 旁邊。
- GUI 支援 **English / 正體中文**（依 Windows 顯示語言自動切換）。

## 快速開始（Windows 執行檔）

執行檔可以自行建置（見[打包執行檔](#打包執行檔)）；如果 Releases 頁面有提供，也可以直接下載。

### 1. Server：`VeilTunnel-Server.exe`

放在有公網 IP 的機器上，或是設好 port forwarding 的路由器後面。

1. 執行 `VeilTunnel-Server.exe`，程式會自動產生一組隨機 PSK。
2. 選擇 port（預設 **443**），保持 **TLS** 開啟。憑證和私鑰欄位都留白，就會使用自動產生的自簽憑證。
3. 按 **啟動**。Windows 防火牆詢問時請選擇允許。
4. 如果 Server 在路由器後面，要把該 TCP port 轉發到這台機器。
5. 按 *PSK 金鑰* 旁的 **複製**，用安全的管道把金鑰交給 Client 端。
   *Client 連線網址* 欄位會顯示網址格式：`wss://<你的公網IP或網域>:443/ws/updates`。

### 2. Client：`VeilTunnel-Client.exe`

1. 填入 **伺服器網址** 與 **PSK 金鑰**。
2. 如果 Server 用的是自簽憑證，要取消勾選 **驗證伺服器 TLS 憑證**（請先看[安全注意事項](#安全注意事項)）。
3. 保持勾選 **全部流量走 VPN：連線期間自動設定 Windows 系統 Proxy**。
4. 按 **連線**，燈號變綠色就表示隧道已經建立。
5. 驗證：開啟 <https://httpbin.org/ip>，顯示的應該是 Server 的 IP。

不遵循系統 Proxy 的程式，可以在程式裡直接設定 SOCKS5 或 HTTP Proxy `127.0.0.1:1080`。

## 從原始碼執行

需要 Python 3.10 以上。

```bash
pip install -r requirements.txt

# GUI
python veiltunnel_server.py
python veiltunnel_client.py

# 命令列（和 GUI 共用 *_config.json；命令列參數會覆蓋設定檔）
python server/main.py --psk "<長隨機金鑰>" --port 443
python client/main.py --server wss://your.server:443/ws/updates --psk "<金鑰>" --no-verify
```

執行 `python server/main.py -h` 或 `python client/main.py -h` 可以看到所有參數。
PSK 也可以用環境變數 `VEILTUNNEL_PSK` 提供。

## 打包執行檔

```bat
py -m pip install -r requirements-build.txt
build.bat
```

會產生 `dist\VeilTunnel-Server.exe` 與 `dist\VeilTunnel-Client.exe`
（PyInstaller 單一檔案，不會跳出主控台視窗）。

## 設定

GUI 的設定存在執行檔旁邊的 JSON 檔；從原始碼執行時則存在專案根目錄。範例：
[`server_config.example.json`](server_config.example.json)、
[`client_config.example.json`](client_config.example.json)。

| Server | 預設 | 說明 |
|---|---|---|
| `host` / `port` | `0.0.0.0` / `443` | 監聽位址 |
| `psk` | 隨機 | 預共用金鑰，至少 8 個字元 |
| `tls` | `true` | 提供 `wss://`。只有測試時，或前面有 TLS reverse proxy 時才關閉 |
| `cert` / `key` | 空白 | 憑證檔；空白 = 自動產生自簽憑證（`certs/`） |
| `auto_ban` | `true` | 驗證失敗的 IP 永久封鎖（`banned_ips.txt`） |

| Client | 預設 | 說明 |
|---|---|---|
| `server` | — | `wss://host:port/ws/updates` |
| `psk` | — | 必須與 Server 相同 |
| `verify_ssl` | `true` | 驗證 Server 憑證 |
| `bind` / `port` | `127.0.0.1` / `1080` | 本機 SOCKS5 + HTTP Proxy |
| `rate_limit_mbps` | `0` | 速率上限；0 = 不限速 |
| `system_proxy` | `true` | 連線期間設定 Windows 系統 Proxy |

兩個設定檔都另外支援 `language`（`auto`、`en` 或 `zh-TW`）與 `log_level`。

## 安全注意事項

- **自簽憑證**：關閉憑證驗證後，能攔截你連線的人可以假冒你的 Server。PSK 能阻止他們
  使用你的 Server，但擋不住他們讀取被攔截的流量。要更安全，請幫 Server 設定網域與
  正式憑證（例如 Let's Encrypt），並保持開啟驗證。
- **請使用夠長的隨機 PSK**（按「隨機產生」即可）。驗證使用以時間為基礎的 token，
  所以 Client 與 Server 的時鐘誤差需要在約 ±5 分鐘以內。
- **自動封鎖很嚴格**：只要 PSK 錯一次，或時鐘誤差太大，該 IP 就會被永久封鎖。
  本機 loopback 位址不會被自動封鎖。要解除封鎖，可以用「封鎖名單」分頁，或直接編輯 `banned_ips.txt`。
- 設定檔以**明文**儲存 PSK，請妥善保管；這些檔案已列入 `.gitignore`。

## 限制

- **只支援 TCP。** UDP（遊戲、VoIP、WebRTC 媒體）不會經過隧道。瀏覽器設定 Proxy 後
  通常不會使用 QUIC/HTTP3，但 WebRTC 仍可能直接走 UDP，並洩漏真實 IP。
  如果在意這點，請在瀏覽器裡關閉不經過 Proxy 的 WebRTC UDP。
- **系統 Proxy 的涵蓋範圍**：只有遵循 Windows Proxy 設定的程式會使用它，例如瀏覽器、
  大多數 GUI 程式、許多 .NET/Python 程式。使用 WinHTTP 的服務，以及有自己網路堆疊的程式
  不在範圍內；這類程式請在程式裡自行設定 SOCKS5/HTTP `127.0.0.1:1080`。
- 一般 HTTP（非 HTTPS）的 Proxy 請求採用一個連線一個請求（`Connection: close`）。
  HTTPS 走 `CONNECT`，不受影響。

## 專案結構

```
common/    封包格式、PSK 驗證、限速器、設定檔、共用 GUI 元件
server/    FastAPI app、隧道/relay、封鎖名單、憑證、runner、GUI、CLI
client/    WebSocket 隧道、SOCKS5/HTTP 混合 Proxy、Windows 系統 Proxy、runner、GUI、CLI
assets/    圖示（附重新產生的腳本）
veiltunnel_server.py / veiltunnel_client.py   GUI 進入點（PyInstaller 的打包目標）
```

**傳輸協定**（二進位 WebSocket 訊息）：`[cmd:1][stream_id:2][payload]`，其中 `cmd` 是
`TCP_CONNECT`、`TCP_DATA`、`TCP_CLOSE`、`CONNECT_OK` 或 `CONNECT_FAIL` 其中之一。
位址採用 SOCKS5 格式編碼（IPv4 / 網域 / IPv6）。第一則文字訊息是驗證 token：
`HMAC-SHA256(psk, unix_time // 30)`。

## 免責聲明

VeilTunnel 供個人在有權使用的伺服器與網路上使用。使用者須自行遵守當地法律，
以及所連線網路的使用政策。本軟體依「現狀」提供，不附任何擔保。

## 授權

[MIT](LICENSE)
