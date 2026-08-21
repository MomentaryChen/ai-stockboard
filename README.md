# ai-stockboard

台股看板服務。預設畫面是**台股大盤**（加權指數）看板，另可查個股歷史 K 線、即時報價與基本資料，
並對大盤與個股產生同一套分析結果。

分析分成兩種，可以互相對照：

- **傳統分析** — 規則式技術分析：均線與四大買賣點。已完成。
- **AI 分析** — 由模型對同一份行情資料產生判讀。開發中。

行情資料（TWSE／TPEX 日成交、即時報價、上市櫃清單）來自 [twstock](https://github.com/mlouielu/twstock)，
原始碼收在 `vendor/twstock/`，以 editable 方式安裝，之後為了 AI 分析要調整取數邏輯時可以直接改。
本服務負責資料落地、速率限制、HTTP API、Web UI 與分析。

```
ai-stockboard/
├── deployment/          docker-compose.yml + .env（DB、server、frontend 共用同一份設定）
├── server/              FastAPI (uv) + Dockerfile
├── frontend/            React 19 + Vite + Recharts（pnpm）+ Dockerfile / nginx.conf
├── docs/
│   └── screenshots/     The images this README embeds, captured by the script below
├── tools/
│   └── screenshots/     Playwright script that re-takes them from a running stack
└── vendor/
    └── twstock/         行情資料來源（MIT，含原始 test/）
```

---

## Screenshots

Every image below is captured from a running stack by `tools/screenshots`, not
drawn by hand. Re-running the script after a UI change refreshes the whole set,
which is the only way screenshots in a README stay true to the thing they claim
to show.

### Market board — `/`

The landing view. While the session is open the index is the live one, ticking
every 10 seconds; outside it the board falls back to the last close and says so.
Underneath sit the opening intel for the watchlist, a K-line chart with the
moving averages, and the same Best Four Point verdict individual stocks get —
here applied to the index itself.

![Market board](docs/screenshots/market-dashboard.png)

### Stock view — `/stock/:sid`

History, dividends and analysis for one company. The Best Four Point card
reports its reasoning, not just its verdict: on most days the honest answer is
"no signal", and the reasons are what make that answer useful rather than
frustrating. `修正版` / `twstock` switches between the corrected rules and the
upstream ones — see [四大買賣點：兩套規則](#四大買賣點兩套規則).

![Stock view](docs/screenshots/stock-detail.png)

### Realtime quotes — `/realtime`

Watchlist quotes with the five-level order book, polled every 10 seconds while
the market is open. This is the one view that requires an account, for the
reason set out in [Realtime quotes require sign-in](#realtime-quotes-require-sign-in).

![Realtime quotes](docs/screenshots/realtime-board.png)

<details>
<summary><b>Admin views</b> — the hub, background jobs, the listed-company roster, accounts</summary>

`/admin` gathers everything an ADMIN can do, and leads with whether anything
needs attention.

![Admin hub](docs/screenshots/admin-dashboard.png)

`/admin/jobs` is where a schedule is changed and a job is run by hand. A job
that has missed two cycles says so rather than waiting to be noticed.

![Background jobs](docs/screenshots/admin-jobs.png)

`/admin/stock-codes` covers the roster this service maintains itself, with the
run history behind it — including the skips, which are the heartbeat that proves
the schedule is alive.

![Listed-company roster](docs/screenshots/admin-stock-codes.png)

`/admin/users` handles roles, deactivation and password resets. The first three
columns are masked during capture: they are real account data in whichever
database the run points at, and this image is committed.

![Accounts](docs/screenshots/admin-users.png)

</details>

### Capturing them again

The stack has to be running — the script drives a real browser against a real
API, because a screenshot of mocked data documents the mock.

```bash
cd tools/screenshots
pnpm install
pnpm setup          # once: downloads the Chromium build Playwright drives
pnpm capture        # public views only
```

Views behind a session are skipped, and named in the output, unless credentials
are supplied:

```bash
SHOT_EMAIL=admin@example.com SHOT_PASSWORD=... pnpm capture
```

| Variable | Default | |
|----------|---------|--|
| `SHOT_BASE_URL` | `http://localhost:8100` | The compose frontend. Use `http://localhost:5173` for the Vite dev server. |
| `SHOT_API_URL` | = `SHOT_BASE_URL` | Only a split deployment needs this; nginx and Vite both proxy `/api`. |
| `SHOT_OUT_DIR` | `docs/screenshots` | |
| `SHOT_EMAIL`, `SHOT_PASSWORD` | — | Unset means public views only. A non-ADMIN account gets everything but the admin views. |
| `SHOT_LOCALE` | `zh-TW` | `en` captures the English UI instead. |
| `SHOT_WIDTH`, `SHOT_HEIGHT` | `1440`, `900` | |
| `SHOT_SCALE` | `2` | Device pixel ratio. |
| `SHOT_SETTLE_MS` | `1200` | Pause after the network goes quiet. Recharts animates in JS, so an idle network does not mean a finished chart. |
| `SHOT_ONLY` | — | Comma-separated shot names, for re-taking one. |

The script only reads. It signs in with what it is given and navigates; it never
registers an account or writes to the database.

---

## 快速開始

需要 **Docker**、**uv**、**Node 22+** 與 **pnpm**（`corepack enable pnpm` 即可）。

```bash
# 1. 資料庫（開發時只起 db，前後端跑在本機才有 hot reload）
cd deployment
cp .env.example .env
docker compose up -d db
docker compose ps                     # ai-stockboard-db ... (healthy)

# 2. 後端  http://localhost:8000
cd ../server
uv sync
uv run uvicorn app.main:app --reload --port 8000

# 3. 前端  http://localhost:5173
cd ../frontend
pnpm install
pnpm dev
```

打開 <http://localhost:5173>，進站就是大盤看板。API 文件在 <http://localhost:8000/docs>。

前端 dev server 會把 `/api` proxy 到 `localhost:8000`，開發時不會遇到 CORS。

---

## 部署

### 全部跑在 Docker

三個服務都由 `deployment/docker-compose.yml` 定義，一行指令起完：

```bash
cd deployment
cp .env.example .env            # 還沒複製過的話
docker compose up -d --build
docker compose ps               # db / server / frontend 都要 (healthy)
```

打開 <http://localhost:8100>，進站就是大盤看板。

| 服務 | 內容 | Host port |
|---|---|---|
| `db` | postgres:16-alpine，資料存在 `stockboard-pgdata` volume | `5433` |
| `server` | FastAPI + uvicorn，`server/Dockerfile` | `8000` |
| `frontend` | vite build 產物由 nginx 提供，`frontend/Dockerfile` | `8100` |

nginx 把 `/api` 與 `/docs` proxy 到 `server:8000`，瀏覽器全程同源，所以 CORS 不會參與；
其餘路徑 fallback 到 `index.html` 交給 react-router。啟動順序由 healthcheck 串起來：
`db` healthy 才起 `server`，`server` healthy 才起 `frontend`。

兩件與 build context 有關的事：

- **server 的 build context 是專案根目錄**（`context: ..` + `dockerfile: server/Dockerfile`），
  因為 `server/pyproject.toml` 以 editable path 依賴 `../vendor/twstock`，兩棵樹的相對位置要保留。
- **frontend 的 build context 是 `frontend/`**，多階段建置：`node:22-alpine` 用 corepack 起 pnpm，跑 `pnpm install --frozen-lockfile && pnpm build`，
  產物 COPY 進 `nginx:1.27-alpine`。最終 image 49 MB，不含 Node。

容器內的 `POSTGRES_HOST` / `POSTGRES_PORT` 由 compose 覆寫成 `db` / `5432`；
`.env` 裡那組 `localhost:5433` 是給「server 跑在本機」時用的，兩種跑法共用同一份檔案。

改了程式碼要重新建置：

```bash
docker compose up -d --build server     # 或 frontend
```

### 單一服務部署（不用 Docker）

```bash
cd frontend && pnpm build         # 產生 frontend/dist
cd ../server && uv run uvicorn app.main:app --port 8000
```

server 偵測到 `frontend/dist` 存在時會把它掛在 `/`，用一個 port 就能跑完整站台。

**不要加 `--workers`。** 服務有數項 process 內狀態，多開一個 worker 會讓對 TWSE 的
速率變成兩倍，見 [Deployment is single-process](#deployment-is-single-process)。

---

## API

| Method | Path | 說明 |
|---|---|---|
| GET | `/api/health` | 服務與資料庫狀態 |
| GET | `/api/stocks/search?q=&limit=` | 代碼／名稱搜尋 |
| GET | `/api/stocks/{sid}` | 個股基本資料 |
| GET | `/api/stocks/{sid}/history?months=6&force=false` | 歷史日成交 |
| GET | `/api/stocks/{sid}/analysis/traditional?months=6&rule_set=grs` | 傳統分析：MA5/10/20/60 + 四大買賣點 |
| GET | `/api/analysis/traditional?sids=2330,0050` | Batch 四大買賣點 from cached daily bars only (no TWSE fetch, max 20) |
| GET | `/api/realtime?sids=2330,0050` | 即時報價，最多 20 檔（**需登入**） |
| GET | `/api/market/open?date=&sids=` | Opening intel for one trading day: gap and drift for the index plus up to 20 watchlist codes. Defaults to today in Taipei; cache-only apart from the index's own backfill |
| POST | `/api/auth/register` | 註冊，直接回一組 token |
| POST | `/api/auth/login` | 登入，帳號或 Email 皆可 |
| POST | `/api/auth/refresh` | 換發 token（會輪替 refresh token） |
| POST | `/api/auth/logout` | 撤銷一組 refresh token |
| GET / PATCH | `/api/auth/me` | 讀取／更新自己的資料 |
| POST | `/api/auth/me/password` | 改密碼，登出其他所有裝置，並回一組新 token |
| GET | `/api/users?q=&limit=&offset=` | 使用者列表（ADMIN） |
| GET / PATCH / DELETE | `/api/users/{user_id}` | 檢視／改角色與狀態／刪除（ADMIN） |
| POST | `/api/users/{user_id}/password-reset` | 重設密碼，回傳一次性臨時密碼（ADMIN） |
| GET / PUT | `/api/watchlist` | 自選股，整批讀寫（需登入） |
| GET | `/api/jobs` | Every background job: schedule, last run, next run (**ADMIN**) |
| GET | `/api/jobs/{job_id}/runs?limit=50` | One job's run history (**ADMIN**) |
| PATCH | `/api/jobs/{job_id}/schedule` | Change when a job fires (**ADMIN**) |
| POST | `/api/jobs/{job_id}/run` | Run now; answers 202 and continues server-side (**ADMIN**) |
| POST | `/api/stocks/sync?force=true` | Sync the listing and wait for it; superseded by the above (**ADMIN**) |

`{sid}` 可以是個股代碼，也可以是大盤 `t00`。AI 分析預定放在 `/api/stocks/{sid}/analysis/ai`，與傳統分析平行。

搜尋預設**排除認購(售)權證**（4.2 萬檔，佔全部代碼的 95%），加 `&include_warrants=true` 才會出現。

```bash
curl 'http://localhost:8000/api/stocks/2330/history?months=3'
curl 'http://localhost:8000/api/stocks/2330/analysis/traditional'
curl 'http://localhost:8000/api/analysis/traditional?sids=2330,2317,0050'

# 即時報價要帶 access token，其餘行情端點不用
curl 'http://localhost:8000/api/realtime?sids=2330,6488' -H "Authorization: Bearer $ACCESS_TOKEN"

# 當日開盤情報 -- today by default, any past trading day with ?date=
curl 'http://localhost:8000/api/market/open?sids=2330,0050'
curl 'http://localhost:8000/api/market/open?date=2026-08-20&sids=2330,0050'
```

---

## 四大買賣點：兩套規則

`rule_set` 決定用哪個版本，**預設 `grs`**：

```bash
curl 'http://localhost:8000/api/stocks/2330/analysis/traditional'                    # grs（預設）
curl 'http://localhost:8000/api/stocks/2330/analysis/traditional?rule_set=twstock'   # 對照
```

回應會帶 `rule_set` 欄位，前端「四大買賣點」卡片右上角也可以直接切換。

### 為什麼需要兩套

`BestFourPoint` 不是 twstock 原創，是從 [toomore/grs](https://github.com/toomore/grs)
（`grs/best_buy_or_sell.py`，MIT，Toomore Chiang）移植過來的。逐條比對後發現移植時掉了兩件事：

**一、乖離轉折關卡失效。** grs 的 `bias_ratio()` 結尾有 `[0]`，把 `(bool, 轉折日, 值)` 裡的布林值取出來：

```python
# grs
return self.data.check_moving_average_bias_ratio(..., positive_or_negative=...)[0]
# twstock —— 少了 [0]，回傳整個 tuple
return self.stock.ma_bias_ratio_pivot(self.stock.ma_bias_ratio(3, 6), position=position)
```

非空 tuple 恆為真，於是 `if self.mins_bias_ratio() and any(check)` 退化成 `any(check)`，前置條件形同不存在。

**二、「量縮價不跌／價跌」比錯欄位。**

| | grs | twstock |
|---|---|---|
| 量縮價不跌 | 今收 > **昨收** | 今收 > **昨開** |
| 量縮價跌 | 今收 < **昨收** | 今收 < **昨開** |

昨天收長紅時，一根實際下跌的黑 K 會被標成「價不跌」並可能出 Buy。

其餘六條規則與 pivot 演算法本身（`ma_bias_ratio_pivot` vs grs 的 `__cal_ma_bias_ratio_point`）逐行等價，沒有問題。

### 影響有多大

兩萬組隨機價格序列跑下來：

| | 乖離關卡通過 | 出訊號 |
|---|---|---|
| `grs`（修正版） | 30.9% | **22.3%** |
| `twstock`（原樣） | 恆真 | **100%** |

twstock 版在兩萬組裡**沒有一次回傳 Don't touch**，而且與 grs 版可能給出相反結論。

### 修在哪裡

`server/app/services/analysis/traditional.py` 的 `_GrsBestFourPoint`，用繼承覆寫三個方法。
**`vendor/twstock/` 一行都沒動**——那份是刻意與 PyPI 1.5.1 保持 byte-identical 的快照。

有一個差異刻意保留：grs 的均線四捨五入到小數 6 位，twstock 是 2 位。只在極接近的平手情況下才有差別，
要對齊得連 `Analytics` 一起 fork，不值得。

---

## 大盤（加權指數）

首頁 `/` 是大盤看板：即時指數、K 線與均線、四大買賣點、近 10 日。個股頁在 `/stock/:sid`，即時報價在 `/realtime`。
即時的部分需要登入（見〈[Realtime quotes require sign-in](#realtime-quotes-require-sign-in)〉），K 線與分析則不用。

大盤在後端就是一個 `sid` = **`t00`**，走的是跟個股完全相同的路由：

```bash
curl 'http://localhost:8000/api/stocks/t00'
curl 'http://localhost:8000/api/stocks/t00/history?months=3'
curl 'http://localhost:8000/api/stocks/t00/analysis/traditional'
curl 'http://localhost:8000/api/realtime?sids=t00' -H "Authorization: Bearer $ACCESS_TOKEN"
```

做得到這件事是因為 `server/app/services/market_index.py` 補上了 twstock 沒有的兩塊：

- **看起來像一支股票**：`INDICES` 提供 twstock 上市櫃清單裡沒有的那一列，`codes_service` 查得到 `t00`，
  於是 `/api/stocks/{sid}`、`/history`、`/analysis/traditional` 三個路由一行都不用改。
  搜尋框打「大盤」「加權」「taiex」也會找到它。
- **歷史資料自己抓**：指數不在 `STOCK_DAY` 端點裡，改抓 TWSE 的兩份月報表再依日期合併——
  `MI_5MINS_HIST` 給開高低收（K 線要用），`FMTQIK` 給成交股數／金額／筆數與漲跌點數（四大買賣點的量能條件要用）。
  合併後回傳 twstock 的 `DATATUPLE`，所以 `daily_price` 的快取、`fetch_log` 的月份記錄、傳統分析全部照舊運作。

即時指數走 TWSE MIS 的 `tse_t00.tw` 頻道。twstock 是用上市櫃清單推 `tse_`／`otc_` 前綴的，
清單裡沒有指數會被誤判成上櫃，所以頻道名寫在 `INDICES` 裡，由 `realtime_service` 直接指定。

指數沒有 `nf`（全名）也沒有單量欄位，MIS 回的 payload 少那幾個 key，這部分在 service 層補掉。

---

## Opening intel (當日開盤情報)

The market board opens on **today's session** and leads with the two numbers a
close alone cannot give you:

| | |
|---|---|
| **gap** (跳空) | `open - previous close`. Priced overnight, before the session traded a share. |
| **since open** | `last - open`. What the session itself did with that start. |

A day that gaps up 1% and fades to flat closes in the same place as a day that
opened flat and went nowhere. Only the pair tells them apart, which is why the
board reports both and labels the combination — 開高走低 and its eight siblings.

The date defaults to today **on the exchange's calendar**, not the browser's,
and the picker reaches back over settled sessions; the selected day lives in
`?date=YYYY-MM-DD`, so a board is linkable and Back undoes a date change. The
last-10-days table doubles as a picker — clicking a row moves the board to it.

### Where the numbers come from

Three sources answer the same question and none covers every case, so
`frontend/src/utils/openIntel.ts` normalises them to one shape and the board
prefers them in this order:

1. **Settled daily bars**, via `GET /api/market/open`. Final, and the only
   source carrying turnover and a previous close for a *watchlist* stock on an
   arbitrary date.
2. **The realtime quote**, for today until TWSE publishes the day's report —
   it carries `y` (yesterday's close), which is what makes the gap computable.
   Signed-in only.
3. **The chart's own history**, already on the page. What lets a signed-out
   visitor still read today's board after the report lands, at no extra request.

Mid-session with no quote to read — a signed-out visitor — the board shows the
last settled session and says so rather than showing an empty card.

### Why the endpoint is cache-only

`/api/market/open` reads `daily_price` and does not call the exchange for
watchlist codes, for the same reason the batch 四大買賣點 endpoint does not: a
cold 20-stock watchlist would queue tens of TWSE month-fetches on the very
limiter (3 requests / 5 s) the realtime poll depends on. A stock with nothing
cached is reported as such, not fetched.

The one exception is the index itself, and only for a past date it has no bar
for — the picker reaches further back than the chart's 1/3/6/12-month ranges do,
so the board's own subject would otherwise be unanswerable. That is one sid and
two months, recorded in `fetch_log`, and a no-op once warm. Today is excluded:
its bar does not exist upstream either until the report is published.

---

## 上市櫃名冊為什麼自己維護

twstock 把上市櫃名冊做成兩個 CSV 打包在套件裡，更新方式是 `__update_codes()`
把檔案**原地覆寫回套件目錄**。這在容器裡行不通：那棵樹是 root 的，程式跑在 `appuser`，
就算寫得進去也會在下次重啟消失。結果就是名冊會無聲地過期 —— 本專案 vendor 的那份停在
**2026/03/31**，而 `codes_service.get_stock()` 是 history / analysis / watchlist 共用的守門員，
所以那之後掛牌的每一檔都會回 404，看起來像「這支股票不存在」。

實測那份快照漏掉 **57 檔**真實標的（20 檔股票、6 檔創新板、31 檔 ETF/ETN），
包含 7855 和運租車、4178 永笙-KY、009826 貝萊德世界股票。

所以名冊改放 PostgreSQL 的 `stock_code`，由 `server/app/services/code_sync.py` 維護：

| 階段 | 行為 |
|---|---|
| seed | 表是空的就先灌 twstock 內建快照，**不碰網路** —— 沒有外網也開得起來 |
| refresh | 啟動時與每 `STOCK_CODE_SYNC_INTERVAL_HOURS` 小時，抓 `isin.twse.com.tw` 上市(strMode=2)＋上櫃(strMode=4) 做 upsert |
| retire | 名冊上消失的代碼標記 `is_active=false`，**不刪** —— `daily_price` 與 `watchlist_item` 還指著它，下市公司的歷史也還有價值 |
| prune | 唯獨權證例外：一次同步就退役 29,062 檔，沒人看過期權證的線圖，過保留期（30 天）直接刪，表跟記憶體才有界 |

### 怎麼知道這些 batch job 有沒有正常跑

每一次嘗試 —— 包含「因為還新鮮所以略過」和「失敗」—— 都會寫進 `job_run`。
這是必要的：同步失敗兩個禮拜跟同步「沒事可做」，對 `stock_code` 來說都是**沒有任何改變**，
光看名冊本身分不出來。

管理者登入後從右上角 **排程作業** 進 `/admin/jobs`，可以看到：

- 每個作業的排程、下次執行、上次結果、最後一次成功、以及 **立即執行**
- 點「執行紀錄」進 `/admin/jobs/{job_id}`：開始時間、觸發方式（啟動／排程／手動，手動附帳號）、
  結果、耗時、各作業自己的計數、訊息
- `/admin/stock-codes` 仍然存在，多附上「名冊現在長什麼樣」（可查詢標的數、有沒有對過交易所）

三種結果各代表什麼：

| 結果 | 意思 |
|---|---|
| `成功` | 真的做了事：名冊抓回來寫入、或清掉了憑證 |
| `略過` | 醒來後確認沒事可做（名冊還在間隔內、沒有可清的憑證）—— 這是「批次工作還活著」的心跳 |
| `失敗` | 例如兩個市場都連不上，既有資料原封不動 |

名冊同步的 `訊息` 欄寫著 `Partial:` 的那幾次，是只有一個市場回應：寫了拿到的部分，
但**刻意沒有退役任何代碼**。超過兩個排程週期沒有成功紀錄時，頁面上會出現警示橫幅。
每個作業各保留最近 200 次紀錄。

幾個刻意的決定：

- **每個作業一條背景 daemon thread**。名冊首次抓取要 40 秒以上（上市那頁是 8MB HTML），
  不能卡住 startup 或 compose 的 healthcheck。`/api/health` 一開機就會回應。
- **只有兩個市場都抓成功才會 retire**。否則其中一邊失敗會把整個市場誤判成下市。
- **失敗 10 分鐘後重試**，不是等滿一個週期。
- **新鮮度檢查**：重啟不會重抓，`max(synced_at)` 還在區間內就直接跳過。
- **不跨 replica 協調**。最壞情況是多抓一次同樣的兩頁，upsert 是冪等的；
  為此在 40 秒的爬取上壓一把鎖不划算。要只讓一個副本跑排程，把其他副本的
  `JOBS_SCHEDULER_ENABLED` 關掉即可（關掉後仍可手動執行）。不過本服務目前無論如何
  都只能跑單一 process，見 [Deployment is single-process](#deployment-is-single-process)。
- 下市標的**仍可用完整代碼查到**（`get_stock` 照樣解析、線圖照畫），只是不再出現在搜尋的前綴／名稱比對裡。

### Background jobs

The listing sync is one of several recurring jobs, so the machinery around it is
generic: `app/services/jobs/` holds a registry of job definitions, the two tables
that record them, a runner, and one scheduler thread per job. Adding a job means
adding a definition and a handler; the scheduler, the API and the admin console
are all driven off that list.

| Job | Default schedule | What it does |
|---|---|---|
| `stock_code_sync` | every 24 h (`STOCK_CODE_SYNC_INTERVAL_HOURS`), plus once at startup | reconciles `stock_code` with the exchanges' registry -- see above |
| `refresh_token_cleanup` | daily at 04:10 | deletes expired refresh tokens, and revoked ones past their retention window |

Each attempt lands in `job_run`, whose `stats` column is JSONB rather than a set
of columns: every job counts different things, and the admin table renders
whatever keys that job's definition declares labels for.

#### Schedules are data, guard rails are code

Schedules live in `job_schedule`, and **the row wins over the environment
variable that seeded it**. A schedule changed in the UI has to survive a
container restart, and a running process cannot write back to its own env, so
`STOCK_CODE_SYNC_*` are demoted to first-boot defaults.

- Two kinds: a fixed **interval** (N minutes/hours/days) or a **daily** wall-clock
  time (`HH:MM`, read in `SCHEDULER_TIMEZONE`).
- Saving takes effect **immediately**. The scheduler thread sleeps on an `Event`;
  saving wakes it so it re-reads the row and recomputes when it is next due,
  instead of finishing a sleep that may have been 24 hours long.
- Each job carries its own **floor and ceiling** in
  `app/services/jobs/registry.py`, and an administrator cannot cross them. The
  listing sync's floor is 60 minutes: one run is two multi-megabyte scrapes off a
  shared rate limiter, and this limit protects TWSE's rate limit rather than this
  service's permission model.

#### This is the most privileged surface in the service

So the limits sit in three layers, none of which trusts the one above it (the
reasoning is in `app/routers/jobs.py`):

| Layer | Refuses | Status |
|---|---|---|
| `require_admin`, declared on the router | anyone who is not an ADMIN | 403, or 401 without a token -- and a route added later inherits it |
| `store.set_schedule`, validating the merged schedule | an interval outside that job's range, a malformed time | 400 / 422 |
| the runner's lock and cooldown | a second concurrent run, a held-down run-now button | 409 / 429 with `Retry-After` |

The cooldown is measured from `job_run`, not from process memory, so restarting
the container or asking a different replica does not clear it. Manual runs record
the administrator who started them (`job_run.actor`) and schedule edits record
who saved them (`job_schedule.updated_by`); both are shown back in the UI. Reads
stay behind the same guard, because the run log carries upstream failure messages
and admin usernames -- the anonymous `/api/health` keeps its own much narrower
summary.

Two consequences worth knowing about:

- **Run-now answers 202 and returns.** The work continues on a background thread
  and the page polls the run log. A page listing several jobs cannot hold a
  request open for 40 seconds per job. `POST /api/stocks/sync` still blocks and
  returns the outcome, for scripts that want it in one call; it goes through the
  same lock, cooldown and audit trail.
- **The old `stock_code_sync_run` table is migrated into `job_run` at startup**,
  once, and only when the target is empty. This project has no migration tool, so
  without that step the history would be stranded in a table nothing reads.

```bash
# Run a job now (ADMIN token). Answers 202; the work continues server-side.
curl -X POST -H "Authorization: Bearer $TOKEN"      'http://localhost:8000/api/jobs/stock_code_sync/run'

# Move it to 02:30 every day.
curl -X PATCH -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json'      -d '{"kind":"daily","daily_at":"02:30"}'      'http://localhost:8000/api/jobs/stock_code_sync/schedule'

# Sync and wait for the outcome (~40 s).
curl -X POST -H "Authorization: Bearer $TOKEN"      'http://localhost:8000/api/stocks/sync?force=true'
```

`/api/health` 的 `stock_codes_synced_at` 是 `null` 時，代表名冊還是 twstock 的內建快照，一次都沒對過。

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

所有對外請求都經過 `server/app/throttle.py` 的滑動視窗限流器。該限流器是 process
內狀態，這也是整個服務只能單 process 部署的主因之一——見 [Deployment is single-process](#deployment-is-single-process)。

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
| `SERVER_PORT` / `FRONTEND_PORT` | `8000` / `8100` | docker compose 對外公開的 port |
| `CORS_ORIGINS` | `http://localhost:5173,...` | 允許的來源（走 nginx 時同源，用不到） |
| `CURRENT_MONTH_TTL_SECONDS` | `900` | 當月資料快取秒數 |
| `THROTTLE_MAX_CALLS` / `THROTTLE_WINDOW_SECONDS` | `3` / `5.5` | 上游速率限制 |
| `JWT_SECRET` | （未設，啟動時隨機產生） | access token 的簽章密鑰，見下方「帳號與權限」 |
| `ACCESS_TOKEN_EXPIRE_MINUTES` / `REFRESH_TOKEN_EXPIRE_DAYS` | `30` / `7` | 兩種 token 的有效期 |
| `ADMIN_USERNAME` / `ADMIN_EMAIL` / `ADMIN_PASSWORD` | `admin` / （未設） / （未設） | 啟動時建立的第一個管理員，email 與密碼都設了才生效 |
| `STOCK_CODE_SYNC_ENABLED` | `true` | First-boot default for the listing sync. Once an admin saves a schedule at `/admin/jobs`, the `job_schedule` row wins |
| `STOCK_CODE_SYNC_INTERVAL_HOURS` | `24` | First-boot default for its interval, same as above |
| `JOBS_SCHEDULER_ENABLED` | `true` | Master switch. Off means this process fires nothing on its own (manual runs still work); leave it on for exactly one replica |
| `SCHEDULER_TIMEZONE` | `Asia/Taipei` | Wall clock a "daily at HH:MM" schedule is read in. `TZ` comes from the same .env, so the two agree by default |

---

## 帳號與權限

帳號（username）、Email、密碼為必填，手機選填。角色只有 `ADMIN` 與 `USER` 兩種，
行情 API 中 `/api/health` 與 `/api/stocks/*` 維持公開，只有 `/api/realtime` 需要登入。

### Token

以 access + refresh 兩段式交換：

- **Access token** 是 JWT，預設 30 分鐘，只帶 `sub`（使用者 id）。
  角色**不放進 token**，`get_current_user` 每次都讀資料庫那一列——因此 ADMIN 把某人降權或停用時
  **下一個 request 就生效**，不必等 token 過期。
- **Refresh token** 是不透明隨機字串，資料庫只存 SHA-256 雜湊，預設 7 天。
  每次 `/api/auth/refresh` 都會**輪替**：撤銷舊的、發新的一組。
- 拿**已經輪替掉的** refresh token 再打一次，視為外洩，該使用者**所有** session 一次撤銷。
  前端因此必須把 refresh 收斂成單一請求（`frontend/src/api/client.ts` 的 `refreshPromise`），
  否則多個 API 同時過期會互相踩到，把使用者隨機登出。

### 第一個管理員

`deployment/.env` 設好 `ADMIN_EMAIL` 與 `ADMIN_PASSWORD`，server 啟動時就會建立。
可重複啟動：

- 帳號不存在 → 建立為 ADMIN
- 帳號已存在 → **不會覆寫密碼**（否則 `.env` 等於一個永久的密碼重設後門），
  但如果被降權或停用了會還原成啟用中的 ADMIN——這是刻意留的救援路徑

### 管理員重設密碼

使用者忘記密碼時，由 ADMIN 到 `/admin/users` 按「重設密碼」（或打
`POST /api/users/{user_id}/password-reset`）。服務**沒有寄信功能**，所以整個流程是這樣的：

1. 系統產生一組 14 位的隨機臨時密碼，只把 bcrypt hash 存進資料庫。
2. **明碼只在該次 response 裡出現一次**，畫面上顯示給 ADMIN 自行轉交。
   關掉就再也查不到——server 沒有留副本，弄丟只能再重設一次。
   字元集刻意拿掉了 `0/O`、`1/l/I` 這些看起來像的字，方便對著螢幕手動輸入。
3. 該帳號的 refresh token **全部撤銷**，舊的登入狀態立刻失效。
4. 該帳號被標記 `must_change_password`，進入**受限模式**：
   除了 `GET /api/auth/me` 與 `POST /api/auth/me/password`，其他 API 一律回 403
   `Password reset required`；前端則被 `<PasswordGate>` 固定在 `/change-password`。
5. 使用者用臨時密碼登入後設定自己的新密碼，旗標才會解除。

換句話說，臨時密碼**只能拿來換一組新密碼**，不能拿來瀏覽帳號——即使它經過了聊天室
或 email 這種不安全的管道。改密碼時仍然要輸入「目前密碼」（也就是那組臨時密碼），
強制與自願兩種情境走同一條路徑。

ADMIN 不能重設自己的密碼（會被擋成 400），要改自己的密碼請走
`POST /api/auth/me/password`。

`POST /api/auth/me/password` 成功後會**回傳一組新的 token**。改密碼會撤銷該帳號
所有的 refresh token（包含當下這台），所以 server 必須重新發一組給剛剛證明自己知道
新密碼的這個 session；否則使用者會在 access token 過期（最多 30 分鐘）後莫名被登出。

`JWT_SECRET` 沒設時，server 仍然會啟動，改用一把隨程序產生的隨機密鑰並記一筆 WARNING。
代價是**每次重啟所有人的 access token 失效**（refresh token 存在資料庫，客戶端會自動換發，使用者無感），
而且**不能跑多個 uvicorn worker**（各自的密鑰不同，會互相拒絕；這只是其中一項限制，
完整清單見 [Deployment is single-process](#deployment-is-single-process)）。正式環境請設定：

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

### 自選股

登入後自選股存在資料庫（`watchlist_item`，最多 20 檔）；未登入則沿用 localStorage。
第一次登入時會把 localStorage 那份**聯集**進帳號，然後清掉本機那份——
不清的話，在同一台瀏覽器換帳號登入會把前一個人的清單帶進去。

The realtime board scores each watchlist sid with the same 四大買賣點
(Buy / Sell / Don't touch) as the stock page, via `GET /api/analysis/traditional`.
That batch route reads `daily_price` only -- it does not call TWSE/TPEX -- so a
20-stock board cannot crowd the shared 3-per-5s limiter that realtime quotes
already use. A sid with no cached bars shows 資料不足; opening the stock page
fills the cache the usual way.

`/realtime` 改成需要登入之後，未登入已經沒有介面可以編輯自選股，
localStorage 那條路留著是為了把**這個改動之前**存下來的清單接進帳號，`useWatchlist` 的兩套儲存不需要動。

```bash
curl -X POST localhost:8000/api/auth/login -H 'Content-Type: application/json' -d '{"identifier":"admin@example.com","password":"..."}'
curl localhost:8000/api/watchlist -H "Authorization: Bearer $ACCESS_TOKEN"
```

---

## Interface language (i18n)

The UI ships in **Traditional Chinese (default) and English**, switched from the
top right. The choice is stored in the **browser's localStorage** under
`ai-stockboard.locale`. A visitor who has never chosen gets one picked from
`navigator.languages`: `zh*` becomes Chinese, `en*` becomes English, anything
else falls back to Chinese.

The language is a property of the device somebody reads on, not a column on
their account -- so it applies before anyone signs in, and the server neither
stores nor consumes it.

```
frontend/src/i18n/
├── locales/zh-TW.ts   the catalogue, and the source MessageKey is derived from
├── locales/en.ts      typed Record<MessageKey, string> -- a missing key fails the build
├── I18nProvider.tsx   context + useI18n(); also syncs <html lang> and the tab title
├── storage.ts         localStorage read/write plus browser-language detection
└── serverText.ts      lookup for the Chinese vocabulary the API returns
```

Usage is `const { t, locale, intlTag } = useI18n()`, where `t('key', { name: v })`
substitutes `{name}` placeholders. Switching language replaces `t` and nothing
else -- there is no `key` on the tree, so charts keep their zoom and react-query
keeps its cache across a switch.

No i18n dependency was added. At this size -- around 250 messages, two locales,
no plural rules in either -- a typed catalogue plus one context is smaller and
easier to read than i18next, and it buys the compile-time completeness check
that a runtime library cannot give.

### What the server sends back

Some product copy is owned by the server on purpose: the Best Four Point reasons
(`services/analysis/traditional.py` plus twstock's `BEST_BUY_WHY` /
`BEST_SELL_WHY`), the market and instrument types from the exchange ISIN
listing, and the job names, descriptions and stat columns in
`services/jobs/registry.py`. Every one of those is a **closed set**, so
`serverText.ts` translates them by lookup rather than the API growing a
translation endpoint.

Two rules make that safe:

- **Anything unknown falls through unchanged.** A reason string added by a newer
  twstock, or a job registered tomorrow, renders in the server's own words
  instead of going blank.
- **Job names and stat columns are keyed on the job id and the stats key**, both
  English identifiers, not on the Chinese label -- so rewording a label upstream
  cannot silently drop its translation.

### Deliberate trade-offs

- **Company names are not translated.** 台積電 is the name of the instrument;
  an English reader searching for it needs the string the exchange publishes.
- **Large numbers use each language's own grouping**: Chinese counts in 萬 (10^4)
  and 億 (10^8), English in K/M/B (10^3/10^6/10^9). `fmtCompact` and
  `fmtLotsAxis` in `utils/format.ts` branch on the locale rather than
  transliterating one into the other.
- **Dates and times go through `intlTag`**, not a hardcoded `'zh-TW'`.

### Adding a string

Add the key to `locales/zh-TW.ts`; `tsc -b` then fails until `locales/en.ts`
covers it too. Never hardcode UI copy in a component -- that is what the
compile-time check exists to catch.

---

## Realtime quotes require sign-in

`/api/realtime` is the only market-data route behind a sign-in. History, search
and the rule analysis all answer out of the PostgreSQL cache, but a quote is
worthless unless it is fresh, so every call genuinely reaches TWSE MIS and spends
part of a budget the **whole service shares** -- the upstream limit is 3 requests
per 5 seconds per source IP, and every open board burns one every 10 seconds.
Requiring an account is what keeps that budget attributable.

Nobody is thrown out of a page:

| Page | Signed out | What signing in adds |
|---|---|---|
| 大盤 `/` | Last trading day's close, K-line and moving averages, 四大買賣點, last 10 days | Intraday index level, refreshed every 10s |
| 個股 `/stock/:sid` | Same, plus the instrument's basics | Intraday price, volume, quote timestamp |
| 即時報價 `/realtime` | A card explaining what the page offers, with sign-in / register | The watchlist board and bid/ask depth |

Three things on the frontend make that work:

- `useLiveQuote` issues no request while signed out (`enabled`), so the view falls
  back to the last-close path that already existed -- written for weekends, not
  added for this feature.
- The 盤中 / 收盤 badge reads `intraday` (what the numbers on screen actually are)
  rather than `isMarketOpen()`. Without this, a signed-out visitor during market
  hours would see yesterday's close labelled 盤中.
- `SignInPrompt` has two variants: full-page for `/realtime`, inline in place of
  the 自動更新 toggle on 大盤 / 個股. Both put the current path into router state,
  so signing in or registering returns to the page the visitor started on.

With no token, or an expired one, the server answers **401 rather than 403** --
`app/deps.py` records why: the frontend interceptor refreshes on 401 and does not
retry on 403, and a request that repeats every 10 seconds cannot afford to be
signed out mid-poll.

`/api/realtime` takes `get_current_user`, so it also inherits that dependency's
403: `Password reset required`, for an account still holding an ADMIN-generated
password. The frontend never reaches it -- `<PasswordGate>` wraps the whole route
table, pinning such an account to `/change-password`, so the 大盤 and 個股 pages
never render and nothing polls.

---

## Deployment is single-process

**This service can only run as one process.** Not "should preferably" — four
separate pieces of state live in process memory, and a second process silently
gets its own copy of each:

| In-process state | Where it lives | What a second process does to it |
|---|---|---|
| The TWSE rate-limit window | `server/app/throttle.py` — a module-level `SlidingWindowThrottle`: a `deque` behind a `threading.Lock` | Each process throttles only itself, so N processes hit TWSE at N × `THROTTLE_MAX_CALLS` per `THROTTLE_WINDOW_SECONDS`. The 3 / 5.5 s default is there because TWSE bans clients that exceed 3 requests per 5 seconds — two workers are already over the line. |
| "Is this job already running?" | `server/app/services/jobs/runner.py` — `_locks: dict[str, threading.Lock]` | `JobBusyError` can only fire against a run in the same process, so two processes will happily run the same job at the same time. |
| The listed-instrument snapshot (~44k rows) | `server/app/services/codes.py` — `_snapshot` | Every process pays the memory, and `invalidate()` after a sync clears only the caller's copy. A database-backed snapshot has no TTL, so the other processes keep serving the pre-sync listing until they happen to restart. |
| `JWT_SECRET`, when it is not set | `server/app/security.py` — `secrets.token_urlsafe(48)`, resolved once at import | Each process signs with a different key, so a token minted by one is rejected by the others and the user bounces between signed-in and signed-out. |

The rate limit is the one that matters, because the entire caching design in
[資料為什麼要落地](#資料為什麼要落地) exists to stay under it. The other three
degrade; that one gets the deployment banned by the upstream.

Nothing enforces the constraint. `server/Dockerfile` runs `uvicorn` without
`--workers`, which is correct, but it is correct by convention — no code refuses
to start when a second process is already live. Setting
`JOBS_SCHEDULER_ENABLED=false` on the extra replicas covers the scheduler only;
the throttle, the snapshot and the signing key are untouched by it.

### What horizontal scaling would take

Each item has to move out of process memory before a second process is safe:

- the rate-limit window into a shared counter — Redis, or a timestamp table
  guarded by a PostgreSQL advisory lock;
- the per-job lock into a PostgreSQL advisory lock keyed on the job id, which
  also retires `JOBS_SCHEDULER_ENABLED` as a hand-run leader election;
- the listing snapshot behind a shared invalidation signal, or a version column
  each process can check cheaply before serving from its own copy;
- `JWT_SECRET` into a required setting, dropping the per-process fallback.

None of that is scheduled. At the current size one process with a thread pool is
enough, and stating the limit is more useful than a scaling story the code does
not support.

---

## 已知限制

- **這個服務目前只能單 process 部署。** 限流視窗、job 鎖、名冊快照與未設定時的
  `JWT_SECRET` 全都是 process 內狀態，跑第二個 process 會直接違反 TWSE 的速率限制。
  見 [Deployment is single-process](#deployment-is-single-process)。
- **即時報價需要登入**，未登入只看得到最近一個交易日的收盤（頁面不會被擋掉，見上一節）。
- **即時報價只在交易時段有效**（週一至週五 09:00–13:30）。非交易時段來源會回最後一筆或空值，UI 有提示。
- **上櫃（TPEX）資料比上市晚一天**發布，屬於來源行為。
- 首次查詢 1 年區間需要 12 個對外請求，受速率限制約需 **18 秒**；之後走快取。
- 四大買賣點需要至少 12 個交易日，不足時回傳「資料不足」。
- **The English UI covers interface copy only.** Stock names and industry groups
  stay as the exchange publishes them; server error `detail` strings are already
  English by convention.
- 即時報價不寫入資料庫，只有歷史日成交落地（大盤的日線同樣落在 `daily_price`，sid = `t00`）。
- 大盤看板非交易時段顯示最近一個交易日的收盤。13:30 收盤到 TWSE 發布當日報表之間，
  日線還是前一天，此時改用 MIS 的最後成交值，避免看板倒退一天。
- 目前只接了加權指數。櫃買指數（`o00`）的即時頻道可用，但歷史報表端點不同，尚未接。
- 上市櫃名冊預設每 24 小時才對一次。當天早上剛掛牌的標的最久要等一天才查得到，
  急用可到 `/admin/stock-codes` 按「立即同步」。
- Scheduling is per process; there is no leader election across replicas. Run with
  `JOBS_SCHEDULER_ENABLED=true` on exactly one of them, or every job runs several
  times over -- harmless, since they are idempotent, but wasted work. Note that
  this switch covers the scheduler only, not the other per-process state: see
  [Deployment is single-process](#deployment-is-single-process).
- Run history is capped at the most recent 200 attempts per job and lives only in
  the database. Nothing alerts anywhere; someone has to look at `/admin/jobs`.
- 四大買賣點只讀成交量、開盤、收盤三個欄位，且只比較最新一根與前一根 K 棒，沒有趨勢或部位概念；
  籌碼面（法人買賣超、融資融券）完全不在裡面。
- 同一套規則現在也跑在大盤 `t00` 上。這是工程上的一致性選擇，不是因為該方法原本適用於指數——
  指數的「量」是全市場成交股數，性質與單一個股的量能不同。
- grs 與 twstock 都沒有為四大買賣點提供書目出處，可驗證的「標準」只到 grs 這份參考實作為止。
- 資料表用 `Base.metadata.create_all` 在啟動時建立。它只建立**不存在的表**，永遠不會 ALTER 既有的表。
  既有的表要加欄位，改在 `server/app/schema_patches.py` 補一行冪等的
  `add column if not exists`，每次啟動都會跑一次。那裡只放**加欄位**——
  改型別、改名、刪欄位都不適合無人值守地對著正在跑的資料庫執行，需要時仍應導入 Alembic。
- 重設密碼產生的臨時密碼**只顯示一次**，且只能靠 ADMIN 自己轉交。沒有寄信、沒有簡訊，
  也沒有「忘記密碼」的自助流程——使用者一定要找得到管理員。
- **Token 存在 localStorage**，任何 XSS 都讀得到。專案沒有 cookie/CSRF 基礎建設，
  nginx 與 Vite proxy 都已原樣轉發 `Authorization`，所以先採 Bearer；access token 的短效期限制了外洩的影響範圍。
- **登出後既有的 access token 仍然有效到過期為止**（最多 30 分鐘）。這是無狀態 token 的固有取捨；
  refresh token 會立刻撤銷，所以 session 無法續期。
- 使用者資料表名為 `app_user` 而不是 `user`——`user` 是 PostgreSQL 保留字，
  而且 `select * from user` **不會報錯**，它回傳的是目前的連線帳號。手寫 SQL 時請用 `app_user`。
- 前端用 **pnpm**（`packageManager` 欄位鎖 11.0.8，靠 corepack 生效）。pnpm 11 預設擋掉依賴的 install script，
  `frontend/pnpm-workspace.yaml` 的 `allowBuilds` 放行 esbuild —— 沒有它 `vite build` 會缺平台 binary。
- 前端 Recharts 停在 2.x（3.x 有 breaking changes）。K 線是 range bar + 自訂 shape，見 `frontend/src/components/Candlestick.tsx`。

## 資料檢查

```bash
cd deployment
docker compose exec db psql -U stockboard -d stockboard \
  -c "select sid, count(*), min(date), max(date) from daily_price group by sid;"
docker compose exec db psql -U stockboard -d stockboard -c "select * from fetch_log order by sid, year, month;"
docker compose exec db psql -U stockboard -d stockboard -c "select id, username, email, role, is_active from app_user order by id;"
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
