# ai-stockboard

台股看板服務。查歷史 K 線、即時報價、個股資料，並對個股產生分析結果。

分析分成兩種，可以互相對照：

- **傳統分析** — 規則式技術分析：均線與四大買賣點。已完成。
- **AI 分析** — 由模型對同一份行情資料產生判讀。開發中。

行情資料（TWSE／TPEX 日成交、即時報價、上市櫃清單）來自 [twstock](https://github.com/mlouielu/twstock)，
原始碼收在 `vendor/twstock/`，以 editable 方式安裝，之後為了 AI 分析要調整取數邏輯時可以直接改。
本服務負責資料落地、速率限制、HTTP API、Web UI 與分析。

```
ai-stockboard/
├── deployment/          docker-compose.yml + .env（DB 與 server 共用同一份設定）
├── server/              FastAPI (uv)
├── frontend/            React 19 + Vite + Recharts
└── vendor/
    └── twstock/         行情資料來源（MIT，含原始 test/）
```

---

## 快速開始

需要 **Docker**、**uv**、**Node 18+**。

```bash
# 1. 資料庫
cd deployment
cp .env.example .env
docker compose up -d
docker compose ps                     # ai-stockboard-db ... (healthy)

# 2. 後端  http://localhost:8000
cd ../server
uv sync
uv run uvicorn app.main:app --reload --port 8000

# 3. 前端  http://localhost:5173
cd ../frontend
npm install
npm run dev
```

打開 <http://localhost:5173>。API 文件在 <http://localhost:8000/docs>。

前端 dev server 會把 `/api` proxy 到 `localhost:8000`，開發時不會遇到 CORS。

### 單一服務部署

```bash
cd frontend && npm run build      # 產生 frontend/dist
cd ../server && uv run uvicorn app.main:app --port 8000
```

server 偵測到 `frontend/dist` 存在時會把它掛在 `/`，用一個 port 就能跑完整站台。

---

## API

| Method | Path | 說明 |
|---|---|---|
| GET | `/api/health` | 服務與資料庫狀態 |
| GET | `/api/stocks/search?q=&limit=` | 代碼／名稱搜尋 |
| GET | `/api/stocks/{sid}` | 個股基本資料 |
| GET | `/api/stocks/{sid}/history?months=6&force=false` | 歷史日成交 |
| GET | `/api/stocks/{sid}/analysis/traditional?months=6` | 傳統分析：MA5/10/20/60 + 四大買賣點 |
| GET | `/api/realtime?sids=2330,0050` | 即時報價（最多 20 檔） |

AI 分析預定放在 `/api/stocks/{sid}/analysis/ai`，與傳統分析平行。

```bash
curl 'http://localhost:8000/api/stocks/2330/history?months=3'
curl 'http://localhost:8000/api/stocks/2330/analysis/traditional'
curl 'http://localhost:8000/api/realtime?sids=2330,6488'
```

---

## 資料為什麼要落地

TWSE 有 **每 5 秒 3 個 request** 的限制，超過會被 ban。分析要跑得快、AI 之後要反覆讀同一段歷史，
都不能每次都回頭打交易所。

歷史日成交除了當月以外不會再變，所以：

- 每個 `(股票, 年, 月)` 只向來源抓一次，寫進 `daily_price`
- `fetch_log` 記錄抓過哪些月份；之後同一支股票不管怎麼切區間都直接走資料庫
- 當月資料 15 分鐘過期才重抓（`CURRENT_MONTH_TTL_SECONDS`）
- 抓到 0 筆的月份也會過期重試，避免把來源的暫時性失敗永久快取

實測：2317 抓 12 個月第一次 **17.9 秒**（卡在速率限制），第二次 **瞬回**。

所有對外請求都經過 `server/app/throttle.py` 的滑動視窗限流器。

---

## 分析架構

```
server/app/services/analysis/
├── __init__.py
├── traditional.py     規則式：均線 + 四大買賣點
└── (ai.py)            AI 分析，開發中
```

兩種分析吃同一份 `daily_price` 資料，各自獨立產生結果，端點也分開，
所以可以對同一支股票同時取得兩種判讀來比較。

傳統分析沒有自己重寫演算法：`_CachedStock` 把資料庫的資料餵回 twstock 的
`Analytics` / `BestFourPoint`，因此結果與該套件本身一致，也不會多打一次交易所。

```
本服務  : close 2375.0  MA5 2380.0  MA10 2389.5  MA20 2358.25  buy / 量縮價不跌
twstock : close 2375.0  MA5 2380.0  MA10 2389.5  MA20 2358.25  (True, '量縮價不跌')
```

## vendor/twstock

上游 twstock 的原始碼快照，經比對與 PyPI 1.5.1 完全相同（僅換行符差異），版本固定為 `1.5.1`。

保留：套件原始碼、`test/`（改動 library 時的安全網）、`LICENSE`（MIT，需保留姓名標示）、上游 README。
移除：`docs/`、上游 CI 設定、`flit.ini`／`Pipfile`／`MANIFEST.in` 等舊打包檔 — 對本服務沒有用途。

```bash
cd vendor/twstock && uv run --with vcrpy python -m unittest discover -s test
```

注意：26 個測試中有 5 個會 error（`test_analytics`、`test_cli`、`test_stock` 的 setUpClass）。
原因是上游用 VCR 錄下的 HTTP 回應綁定了錄製當時的年月，而 `Stock.fetch_31()` 是以「今天」
往回算月份，時間一過就對不上錄影帶。這是上游測試本身的時效問題，與本專案的改動無關；
其餘 20 個離線測試（含均線、四大買賣點的純運算部分）皆通過。

---

## 設定

只有一份設定檔：**`deployment/.env`**（參考 `.env.example`）。
Docker Compose 會自動讀它，API server 也讀同一份（`server/app/config.py`），
所以資料庫帳密只需要寫一次，兩邊不會對不上。

| 變數 | 預設 | 說明 |
|---|---|---|
| `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` | `stockboard` | DB 帳密與資料庫名，容器與 server 共用 |
| `POSTGRES_HOST` / `POSTGRES_PORT` | `localhost` / `5433` | server 連線位置；5433 避免撞到既有的 5432 |
| `DATABASE_URL` | （未設） | 設了就蓋過上面組出來的連線字串，用來指向外部託管資料庫 |
| `CORS_ORIGINS` | `http://localhost:5173,...` | 允許的來源 |
| `CURRENT_MONTH_TTL_SECONDS` | `900` | 當月資料快取秒數 |
| `THROTTLE_MAX_CALLS` / `THROTTLE_WINDOW_SECONDS` | `3` / `5.5` | 上游速率限制 |

---

## 已知限制

- **即時報價只在交易時段有效**（週一至週五 09:00–13:30）。非交易時段來源會回最後一筆或空值，UI 有提示。
- **上櫃（TPEX）資料比上市晚一天**發布，屬於來源行為。
- 首次查詢 1 年區間需要 12 個對外請求，受速率限制約需 **18 秒**；之後走快取。
- 四大買賣點需要至少 12 個交易日，不足時回傳「資料不足」。
- 即時報價不寫入資料庫，只有歷史日成交落地。
- 資料表用 `Base.metadata.create_all` 在啟動時建立。schema 目前穩定，日後要改欄位再導入 Alembic。
- 前端 Recharts 停在 2.x（3.x 有 breaking changes）。K 線是 range bar + 自訂 shape，見 `frontend/src/components/Candlestick.tsx`。

## 資料檢查

```bash
cd deployment
docker compose exec db psql -U stockboard -d stockboard \
  -c "select sid, count(*), min(date), max(date) from daily_price group by sid;"
docker compose exec db psql -U stockboard -d stockboard -c "select * from fetch_log order by sid, year, month;"
```

清掉快取重來：

```bash
cd deployment && docker compose down -v && docker compose up -d
```

---

## 授權

MIT — 見專案根目錄的 [`LICENSE`](../LICENSE)。

`vendor/twstock/` 為上游 twstock（Copyright (c) 2017-2024 Louie Lu，MIT），
原始 `LICENSE` 與姓名標示已完整保留於 `vendor/twstock/LICENSE`。
