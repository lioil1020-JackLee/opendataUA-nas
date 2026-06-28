# OpenData Weather UA

OpenData Weather UA 是一個將中央氣象署 OpenData 即時觀測資料轉成 OPC UA 節點的 Python 專案，並提供 FastAPI Web UI 讓使用者管理設定、查看天氣資料、啟停 OPC UA Server 與即時查看日誌。

專案目前主要面向 Synology NAS / Docker Compose 部署，也可以在一般 Python 3.12+ 環境中直接執行。

## 功能

- 從中央氣象署 OpenData API 讀取指定測站的即時氣象資料。
- 建立 OPC UA Server，將每個測站資料發布成 OPC UA 變數節點。
- 提供 Web UI 管理 `data/config.json`、啟動/停止 OPC UA Server、查看執行日誌。
- Web UI 內建 watchdog，容器運行期間會自動檢查並重啟 OPC UA Server。
- 支援將資料 mirror 寫入其他 OPC UA endpoint。
- 提供 Dockerfile、docker-compose.yml、NAS 首次安裝與更新腳本。

## 專案結構

```text
.
├── main.py                     # 程式入口，預設啟動 Web UI
├── server/
│   └── opcua_server.py          # OpenData 擷取與 OPC UA Server
├── webui/
│   ├── app.py                   # FastAPI backend
│   └── static/index.html        # Web UI 前端
├── data/
│   └── config.example.json      # 設定檔範本
├── docs/
│   ├── 更新容器.md
│   └── 重建容器.md
├── Dockerfile
├── docker-compose.yml
├── setup.sh                     # NAS 首次安裝腳本
└── update.sh                    # NAS 更新腳本
```

## 需求

- Python 3.12+
- Docker / Docker Compose（使用 NAS 或容器部署時）
- 中央氣象署 OpenData API 授權碼

Python 套件依賴定義在 `pyproject.toml`：

- `asyncua`
- `fastapi`
- `uvicorn[standard]`
- `python-multipart`
- `pillow`
- `pystray`

## 設定檔

正式執行需要建立本機設定檔：

```bash
cp data/config.example.json data/config.json
```

請編輯 `data/config.json`，至少設定：

- `openData.address`：OpenData API base URL。
- `openData.api`：資料集 ID，預設為 `O-A0003-001`。
- `openData.auth_key`：中央氣象署 OpenData 授權碼。
- `openData.stations`：要讀取的測站清單。
- `opcUA.url`：本機 OPC UA Server endpoint，預設 `opc.tcp://0.0.0.0:48484`。
- `opcUA.mirror_endpoints`：要同步寫入的遠端 OPC UA endpoint。
- `opcUA.mirror_station_map`：本機測站 ID 與遠端測站節點名稱對應。
- `intervals.weather_fetch_seconds`：天氣資料更新週期。
- `intervals.mirror_push_seconds`：mirror 寫入週期。

`data/config.json` 已加入 `.gitignore`，不要提交真實 API key 或內部 endpoint。版本庫只保留 `data/config.example.json`。

## 本機執行

安裝依賴：

```bash
pip install -e .
```

或使用 `uv`：

```bash
uv sync
```

啟動 Web UI 與 OPC UA Server 管理器：

```bash
python main.py
```

等同於：

```bash
python main.py web --host 0.0.0.0 --port 8188
```

只啟動 OPC UA Server：

```bash
python main.py server --config data/config.json
```

預設服務位址：

- Web UI: `http://127.0.0.1:8188`
- Health check: `http://127.0.0.1:8188/api/health`
- OPC UA: `opc.tcp://127.0.0.1:48484`

## Docker / NAS 部署

先確認 `data/config.json` 已存在並填入有效設定。

首次安裝：

```bash
chmod +x setup.sh update.sh
./setup.sh
```

一般更新：

```bash
./update.sh
```

若 Dockerfile 或 Python dependency 有變更，重新 build：

```bash
./update.sh --build
```

常用 Docker Compose 指令：

```bash
docker compose logs -f opendata-ua
docker compose up -d --force-recreate opendata-ua
docker compose down
```

`docker-compose.yml` 使用 host network，容器內服務預設使用：

- Web UI port: `8188`
- OPC UA port: `48484`

## Web API

Web UI backend 提供以下主要 API：

- `GET /api/health`：健康檢查。
- `GET /api/config`：讀取目前設定。
- `PUT /api/config`：儲存設定。
- `GET /api/weather`：依目前設定向 OpenData API 讀取測站資料。
- `GET /api/server/status`：查詢 OPC UA Server 狀態。
- `POST /api/server/start`：啟動 OPC UA Server。
- `POST /api/server/stop`：停止 OPC UA Server。
- `GET /api/logs`：以 Server-Sent Events 串流輸出日誌。

## OPC UA 節點

OPC UA Server 會在 `Weather` object 底下依測站 ID 建立節點，並發布以下欄位：

```text
24R, D_TN, D_TS, D_TX, ELEV, H_10D, H_F10, H_FX, H_UVI,
H_XD, HUMD, PRES, TEMP, WDIR, WDSD, CITY, D_TNT, D_TXT,
H_F10T, H_FXT, VIS, Weather
```

這些欄位會由中央氣象署 OpenData response 轉換而來，並以字串寫入本機 OPC UA 節點；mirror 寫入遠端 OPC UA endpoint 時會依遠端節點型別嘗試轉型。

## 注意事項

- `data/config.json` 是本機執行設定，包含 API key 時不可提交。
- Web UI 會把日誌寫入 `data/server.log`。
- `main.py ui` 子命令目前會引用 `ui.desktop_ui`，但此 repository 目前沒有包含 `ui/` 目錄；建議使用 Web UI 或 server 模式。
- 若在 Linux/NAS 上需要停止外部 OPC UA process，`webui.app` 會優先使用 `lsof` 查詢 port 對應 PID；沒有安裝 `lsof` 時只能判斷 port 是否開啟。

## License

本專案使用 MIT License，詳見 `LICENSE`。
