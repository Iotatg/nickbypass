# linkresolver

把短链 / 广告门解析到**真实终点**：HTTP 快路径优先，卡住就上无头 Chromium 点掉倒计时。
每一步跳转都留痕，解析不出来就直说卡在哪，不编链接。

```
https://v.gd/CCGtUT
  └─ 200  → 页面按钮「https://example.com/hello」直达终点
       └─ 200  https://example.com/hello   ✓
```

---

## 快速开始

```bash
cd linkresolver
pip install -r requirements.txt          # httpx / bs4 / playwright / fastapi / uvicorn
playwright install chromium              # 若系统没有 chromium

# 命令行
python -m linkresolver https://bit.ly/xxxx
python -m linkresolver url1 url2 url3 --json
python -m linkresolver https://gplinks.co/xxxx --browser      # 强制浏览器模式
python -m linkresolver https://ouo.io/xxxx --no-browser       # 只用 HTTP，快
python -m linkresolver https://x.y/z --headful                # 显示浏览器窗口，调试用

# 网页
python -m uvicorn linkresolver.server:app --port 8000
# 打开 http://localhost:8000
```

作为库：

```python
from linkresolver import resolve

r = resolve("https://tinyurl.com/xxxx")
print(r.ok, r.final_url, r.method)
for s in r.steps:
    print(s.kind, s.url, s.detail)
```

---

## 它到底怎么解析

### 第一层：HTTP 快路径（毫秒级，不启浏览器）

手动逐跳跟重定向（`follow_redirects=False`），每一跳都记一条 trace；拿到 200 后按顺序试：

| 顺序 | 手法 | 覆盖 |
|---|---|---|
| 1 | 站点专用规则 | `adf.ly/j.gs/q.gs` 的 `ysmm` 变量（奇偶拆位 + base64）、`vk.com/away.php?to=`、`google/url?q=` |
| 2 | base64 参数 | `?url= ?u= ?r= ?s= ?id= ?go= ?to= ?link= ?dest=` —— 大量 safelink 用这套 |
| 3 | `meta http-equiv=refresh` | 老式跳转页、tinyurl 的 `/preview/deprecated/` |
| 4 | JS `location` 赋值 | `location.href=` / `location.replace()` / `top.location=` |
| 5 | 内联 JSON | `{"url":"https://..."}` 这种嵌在 `<script>` 里的 |
| 6 | 页面按钮 | 文字是"Continue / Get Link / 下载"或本身就是 URL 的 `<a>`（v.gd 就是这种） |
| 7 | GET 表单 | 自动把 hidden input 拼成查询串提交（POST 表单留给浏览器） |

### 第二层：浏览器兜底（5–25 秒）

判定为广告门（已知域名名单，或"停在原站 + 有计时器/倒计时/继续按钮"打分 ≥3）时自动启动：

- 无头 Chromium，真实 UA / locale / 1366×800
- `window.open` 打桩成空函数 → 弹窗全部失效
- 路由拦截 doubleclick / popads / propellerads / adsterra 等 40+ 广告域名
- 每 0.9 秒扫一遍页面：找可见、未禁用、文字命中 `continue|get link|proceed|download|verify` 的元素并点击
- 识别 `N seconds` 倒计时并等待
- 判定终点：**离开广告站域名**后的第一个地址；若还停在广告站（跳到 `/explore`、404 页），判为未解析

### 反爬识别

`403/429/503` + `just a moment` / `cf-chl` / `checking your browser` / `g-recaptcha` 等特征 → 直接标记"被拦截"，
并在 trace 里写清楚是哪一条命中。

---

## 验证（本机实测）

**离线单测 16 项全过**（`python3 tests/test_offline.py`）：
meta refresh / JS 相对路径 / base64（含无 padding）/ adf.ly ysmm 往返 / biglink 按钮 /
GET 表单拼参 / 反爬识别 / 静态资源过滤。

**真实短链**（沙箱内实时解析）：

| 输入 | 结果 | 方式 | 耗时 |
|---|---|---|---|
| `tinyurl.com/yjqg3sug` | `https://example.com/hello` | http（302 → preview 页 → meta refresh） | 0.4s |
| `is.gd/q7x9eD` | `https://example.com/hello` | http（301） | 0.3s |
| `v.gd/CCGtUT` | `https://example.com/hello` | http（中间页按钮） | 0.5s |
| `linkvertise.com/123456/test`（无效 ID） | 未解析 | browser | 2.0s |
| `ouo.io/testabc` | 被 Cloudflare 拦（403 just a moment） | — | 0.1s |

**浏览器链路**：本地造了两个门做对照——
`gate.html`（3 秒倒计时 + Get Link 按钮）和 `gate2.html`（终点由 JS 运行时拼接，静态 HTML 里没有任何完整 URL）。
后者在 `--no-browser` 下正确判"需要浏览器"，默认模式下自动兜底：等 3 秒 → 点「Continue」→ 拿到 `target.html`，5.3 秒。

> 沙箱 IP 访问真实 Linkvertise / ouo 会被 CF 或地区策略挡，所以真实广告门的端到端验证需要你在本地拿一条**自己的**链接跑。

---

## 结构

```
linkresolver/
├── linkresolver/
│   ├── engine.py     # 逐跳请求 + 判定"终点 / 广告门 / 被拦"
│   ├── rules.py      # 7 类提取器 + 站点规则 + 域名知识表
│   ├── browser.py    # Playwright：倒计时、点按钮、抓终点
│   ├── models.py     # Step / Result
│   ├── cli.py        # 命令行
│   └── server.py     # FastAPI + 单页 UI
├── tests/
│   ├── test_offline.py
│   └── fixtures/     # gate.html / gate2.html / target.html
├── SURVEY.md         # GitHub 同类项目调研
└── requirements.txt
```

---

## 边界（重要）

- **它不会破解验证码**：遇到 reCAPTCHA / hCaptcha / Turnstile 就是过不去，会如实报错。
- **它不绕登录墙和付费墙**：需要账号的页面只会停在那里。
- **它只做"展开跳转链"**：不代理、不下载、不存储你的链接；Web UI 无数据库、无日志落盘。
- 请只用于你自己点开的链接。拿它批量刷广告收益、绕过付费内容或抓取他人资源，后果自负。
