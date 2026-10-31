# GitHub 上"真能跑"的链接绕过项目 —— 调研结论

> 调研时间 2026-10-31。判断标准只有一条：**源码可读 + 逻辑真实 + 今天还能跑**。
> 凡是 Pyarmor / 加密壳 / 只给 exe / 只给"联系 TG 拿新版"的，一律不算。

---

## 一、现存实现只有三种，没有第四种

| 类型 | 原理 | 能过什么 | 现状 |
|---|---|---|---|
| **A. 纯 HTTP 展开** | 顺着 301/302、`meta refresh`、`location.href`、base64 参数走 | bit.ly / tinyurl / is.gd / v.gd / t.co 这类无广告短链 | 稳定，永远有效 |
| **B. 每站写死 handler + 第三方 API** | 针对 ouo、gdtot、katdrive 等写解析函数，配 `cloudscraper` 过 CF，必要时调 `bypass.vip` / `dcrypt.it` | 2021–2022 年的那批站 | **大半已失效**：站点改版、API 收费、CF 升级 |
| **C. 浏览器自动化 / userscript** | 真开一个浏览器，等倒计时、点"继续"、拦弹窗、抓最终地址 | Linkvertise / GPLinks / ouo / adfoc 等 JS 广告门 | 唯一还通用的路子，但慢（5–25 秒/条） |

**关键结论**：现代广告门（Linkvertise、GPLinks、ouo、shrinkme）的终点是 JS 运行时拼出来的，还带动态 token 和 reCAPTCHA。
纯 `requests` 方案在 2023 年之后基本报废——这就是为什么那批 2022 年的高星仓库现在跑不动。

---

## 二、值得看的仓库（按可用性排序）

### 1. `nOneCode4u/bypass-shortlinks` ★ 最接近"真能用"
- **形态**：单文件 userscript，303 KB，v96.5（2026-10-30 仍在更新），Unlicense
- **做法**：400+ 域名规则，浏览器内 DOM 自动化（等倒计时、点按钮、解 base64、处理 meta、跳过 safelink）
- **优点**：维护活跃、规则最全、无服务端依赖
- **缺点**：只能装在 Tampermonkey 里用，没有 API / CLI；规则是"逐个站点调"，站点改版就要跟进
- **适合**：你自己浏览时顺手跳过广告门

### 2. `TheCaduceus/Link-Bypasser-Bot` ★ 64 · Python · 2022-11
- **做法**：`bypasser.py` 里一个站一个函数（katdrive / hubdrive / gdtot / ouo…），`cloudscraper` 过 Cloudflare，
  重活丢给 `bypass.vip` API、`dcrypt.it`、`xcscxr/ouo-bypass`
- **评价**：代码结构清楚，是"B 类"的教科书样本；但 2022 年的 handler 现在大部分已死，
  且强依赖第三方 API（挂了就全挂）。适合**读**，不适合直接部署。

### 3. `Mid0aria/universal-bypass` ★ 26 · Python · 2023-05
- 单文件 `universalbypass.py`，CLI 交互式，规则集中在字典里。轻量、可读，适合当模板改。

### 4. `TWIST-X7/Aio-Bypasser` ★ 50 · C# / .NET · 2022
- 桌面 GUI，17 个站点。Windows only，需要 .NET SDK 自己编译。已停更。

### 5. `tsubasa652/python-shortlink-extractor` · MIT · 2022
- 唯一一个正经打包成 pip 包的：`extract_short_link(url)`。只做 A 类（纯展开），代码 200 行左右，干净。

### 6. 其他（同类，规模更小）
`bipinkrish/Link-Bypasser-Bot`（TG bot）、`Luiss156/link-gate-jumper`、`Suurp/ShorLink-Master`、
`cyan-n1d3/PSAbypass`、`nsmle/ouo-bypass`、`wendoj/linkvertise-bypasser`。

### 反面样本
你上次给的那个 `ig-magic/NickBypassbot`：Pyarmor 加密 + Windows `.pyd` + "泄露的私密工具"话术，
历史里明文源码是个整蛊脚本（"There is no bypass. There never was."）。**这类一律不碰。**

---

## 三、所以我建了什么

把 A 类和 C 类拼起来，做成一个可审计的本地工具（`./linkresolver/`）：

1. **HTTP 快路径**（毫秒级）：手动逐跳跟 30x，记录每一跳；再依次尝试
   `meta refresh` → JS `location` → 内联 JSON 的 url 字段 → base64 参数（`?url= ?r= ?s=`）→
   页面"继续"按钮 → GET 表单自动提交。adf.ly 的 `ysmm` 变量单独做了静态解码。
2. **浏览器兜底**（秒级）：判定为广告门时，启无头 Chromium，拦掉广告/统计域名，
   等倒计时、点「Continue / Get Link」，抓离开广告站后的第一个地址。
3. **不撒谎**：遇到 Cloudflare 人机验证、登录墙、失效链接，直接报"卡在哪一步"，
   绝不编一个链接给你——这正是大多数 bot 的通病。
4. **两个入口**：CLI（`python -m linkresolver`）+ Web UI（`uvicorn linkresolver.server:app`）。

实测结果见 `README.md` 的「验证」一节。
