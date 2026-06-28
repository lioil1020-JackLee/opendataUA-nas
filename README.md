# OpenData Weather UA

OpenData Weather UA 是一個桌面天氣監看工具，會讀取中央氣象署 OpenData 測站資料，並同步提供本機 OPC UA Server，方便用 SCADA、UAExpert 等 OPC UA Client 連線。

## 專案分析

- `main.py` 是入口，支援 `ui` 與 `server` 兩種模式；未帶參數時預設啟動桌面 UI。
- `ui/desktop_ui.py` 提供 Tkinter 桌面介面，內含站點管理、系統匣最小化、設定編輯，以及背景啟動 OPC UA Server。
- `server/opcua_server.py` 負責抓取 OpenData 測站資料、建立 OPC UA 節點，並定時更新數值。
- `data/config.json` 是唯一的執行期設定檔。
- 打包採用 PyInstaller，規格檔為 `OpendataUA-onedir.spec` 與 `OpendataUA-onefile.spec`。

## 依賴管理

本專案已改為由 `uv` 完整管理依賴：

- 執行期依賴定義於 `pyproject.toml`
- 建置依賴定義於 `pyproject.toml` 的 `build` dependency group
- 鎖定版本由 `uv.lock` 管理
- CI/CD 與本機打包都使用 `uv sync` / `uv run`

## 環境需求

- Python 3.12+
- Windows 10/11 或 macOS
- 已安裝 `uv`

## 安裝

一般執行環境：

```powershell
uv sync --locked
```

若需要打包：

```powershell
uv sync --locked --group build
```

## 執行

啟動桌面 UI：

```powershell
uv run python .\main.py
```

明確指定 UI 模式：

```powershell
uv run python .\main.py ui
```

啟動時最小化到系統匣：

```powershell
uv run python .\main.py -min
```

只啟動 OPC UA Server：

```powershell
uv run python .\main.py server
```

## 打包

Windows 或 macOS 的 PyInstaller 打包也由 `uv` 管理：

```powershell
uv run --group build pyinstaller .\OpendataUA-onedir.spec
uv run --group build pyinstaller .\OpendataUA-onefile.spec
```

如果環境已先執行過 `uv sync --locked --group build`，也可以使用：

```powershell
uv run --locked --no-sync pyinstaller .\OpendataUA-onedir.spec
```

## OPC UA 預設設定

預設 endpoint 來自 `data/config.json`：

```text
opc.tcp://127.0.0.1:48480
```

使用 UAExpert 連線時可採用：

- Security Mode: `None`
- Security Policy: `None`
- User: `Anonymous`

## 設定檔

`data/config.json` 主要欄位：

- `openData.address`：OpenData API 基底網址
- `openData.api`：資料集代碼，預設為 `O-A0003-001`
- `openData.auth_key`：OpenData 授權金鑰
- `openData.stations`：測站清單，格式為 `id` 與 `name`
- `opcUA.url`：OPC UA endpoint
- `opcUA.bind_ip`：伺服器綁定 IP，可選

## 常見問題

系統匣功能無法使用時：

- 確認已安裝 `pystray` 與 `pillow`
- 某些桌面環境可能不支援 tray icon，程式會退回一般視窗最小化行為

OPC UA 無法連線時：

- 確認 `data/config.json` 的 `opcUA.url` 格式正確
- 確認對應埠號未被其他程式占用

## 開發備註

- 請以 `uv add`、`uv remove`、`uv lock` 維護依賴，不再使用 `requirements.txt`
- 若有調整 `pyproject.toml`，請一併更新 `uv.lock`
