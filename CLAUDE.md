# TradingAgents v2 — 项目交接文档（Cowork → Claude Code）

> 本文件用于把之前在 Cowork（本地 agent）里完成的工作和上下文，交接给 Claude Code。
> 放在仓库根目录 `C:\trading\TradingAgents\CLAUDE.md`，Claude Code 启动后会自动读取，作为项目记忆。
> 最后更新：2026-06-22（见 §15、§16、§17 本次会话变更日志）

---

## 1. 项目概览

- **项目**：TradingAgents（官方多智能体股票分析框架）+ 自研 A 股外挂补丁层（v2）。
- **本地路径**：`C:\trading\TradingAgents\`
- **核心价值**：多空研究员对抗辩论 + 风控三方辩论 + 组合经理裁决，对单只股票做深度论证（区别于顺序流水线式分析）。
- **运行形态**：本地 Web 工作台（`web_app`）+ Python venv。
- **LLM**：DeepSeek（推理用英文，输出层翻译成中文；理由见 §6）。

### 设计原则
官方框架尽量不动，所有 A 股适配以**薄外挂层**（约 1500 行）实现，跟随官方上游升级。补丁集中在 `ashare_vendor\`，通过 `install_ashare_patch.py` 安装。

---

## 2. 关键目录与文件

| 路径 | 作用 |
|------|------|
| `tradingagents\agents\` | 官方各智能体，提示词以 `system_message` 字符串内嵌（`analysts\market_analyst.py`、`researchers\bull_researcher.py`、`trader\trader.py`、`risk_mgmt\` 等） |
| `ashare_vendor\` | 自研 A 股外挂层（行情/新闻/基本面/财报/身份解析等） |
| `ashare_vendor\ts_relay.py` | Tushare 中转站客户端，`preferred()` 返回是否启用 |
| `ashare_vendor\context_patch.py` | 向所有智能体注入的 A 股市场上下文（动量/风格/防均值回归指令）。改中文推理实验也在这里 |
| `install_ashare_patch.py` | 补丁安装器，**每次覆盖新文件后必须重跑** |
| `web_app\` | 本地 Web 工作台（结果页、下载/打印按钮在这里） |
| `.env` | 密钥与开关（见 §4） |

---

## 3. 已完成的工作（v2 当前状态）

### 数据源
- **Tushare 中转站全面接入**，六大类全部 Tushare 优先，失败自动回退原链路（通达信本地 / 东财直连）：
  行情日线、公司名称/行业（身份解析第 0 级）、估值指标（daily_basic，支持按分析日取历史值）、三大报表、财联社宏观快讯、股东增减持。
- **Tushare 特色数据**：
  - 给消息面分析师：资金流向、龙虎榜、涨停/连板、融资融券、筹码分布（获利盘胜率）。
  - 给基本面分析师：股权质押、未来一年解禁、回购、业绩预告、业绩快报、机构调研。
  - 宏观：CPI/PMI/GDP/Shibor 快照。
  - 每个子项独立容错、限制行数控制 token，未配置 Tushare 时静默跳过。
- **AKShare 已默认摘除**（行情/新闻/基本面/财报全链路禁用）。关键财务指标改走 Tushare `fina_indicator`。设 `ASHARE_USE_AKSHARE=true` 可恢复。
- **金十数据 MCP 已接入**（标准 JSON-RPC 直连，已用 mock 验证握手/SSE/快讯/搜索/日历）：
  宏观快讯优先级 = Tushare/财联社 → 金十快讯 + 财经日历；个股新闻在东财直连结果后追加金十按公司名搜索的相关快讯。
- **efinance**：评估后**不引入**（爬东方财富，与 akshare 东财接口同源，同样受反爬影响）。

### 分析质量
- **结构化决策卡（已做）**：分析结束用 deepseek-v4-flash 把全部报告压成一张卡——操作建议、信心评分、买入区间、止损、目标位、建议仓位、核心理由、风险警报、结论作废条件、执行清单。硬约束：点位只能从报告原文数字推导，推不出显示 null，禁止编造。
- **完整报告下载/打印（已做）**：结果页顶部两个按钮——「下载完整报告 .md」「下载打印版 .html」，拼接决策卡 + 最终决策 + 四份分析师报告 + 辩论全文 + 风控全文；HTML 版 Ctrl+P 直接打印，辩论详情打印时自动折叠。
- **行情风格修正（已做）**：每次分析用 Tushare 指数数据实时算上证/深成/沪深300/创业板指/科创50 的 20/60 日动量与风格分化度，注入所有智能体，并指令"动量集中型行情中高估值可长期维持，不得仅因历史估值偏高就建议离场，须给出趋势破坏/量能衰竭/政策转向等具体作废信号"。

---

## 4. .env 配置（必需）

```
# Tushare 中转站（官方 15000 积分档功能）
TUSHARE_TOKEN=<你的token>
TUSHARE_HTTP_URL=https://ts.gyzcloud.top/api

# 金十数据 MCP
JIN10_API_KEY=<你的金十key>

# 关闭 AKShare（默认行为；设 true 可恢复）
ASHARE_USE_AKSHARE=false
```

> ⚠️ 安全：TUSHARE_TOKEN 和 JIN10_API_KEY 曾在 Cowork 对话里明文出现过，建议到对应后台**重置一次**密钥，新值只写进 `.env`，不要再贴进聊天。
>
> ✅ **当前状态（2026-06-22 核对，详见 §15）**：`TUSHARE_TOKEN`（中转站 30天套餐，到期约 2026-07-22）、`TUSHARE_HTTP_URL`、`JIN10_API_KEY` 均为真实值且实测可用。`ts_relay.preferred()=True`，金十快讯/搜索/日历正常。24 个 Tushare 接口冒烟：19 个直接可用；宏观4个(cn_cpi/cn_gdp/cn_pmi/shibor)+repurchase 经本 session 修复后可用；`news` 接口未在积分档（403，走金十/东财兜底）。续费只改 `.env` token，不必重跑安装器。

---

## 5. 标准升级流程（每次改代码后）

1. 关闭 Web 工作台
2. 确认 OneDrive 同步完成（文件夹图标显示绿勾）
3. 复制新文件覆盖到 `C:\trading\TradingAgents\`
4. **重跑** `python install_ashare_patch.py`
5. 启动，确认控制台打印 `✅ Tushare 中转已启用（https://ts.gyzcloud.top/api）...`

### 验证 Tushare 是否真正生效（仓库目录、激活 venv 后）
```powershell
python -c "from dotenv import load_dotenv; load_dotenv('.env'); from ashare_vendor import ts_relay; print(ts_relay.preferred())"
```
输出 `True` 才算配置生效。若运行日志里行情失败直接从 AKShare 开始、且没有任何 Tushare 尝试记录 → 说明跑的是旧代码（多半是覆盖没完成或没重跑安装器）。

---

## 6. 重要决策与背景

- **DeepSeek 用英文推理**：官方框架的工具调用协议、few-shot、结构化输出契约全是英文，混语言会增加格式出错率（已出现过一次 structured-output 重试）。输出层翻成中文无信息损失。**2026-06-22 用户拍板：永久保持英文推理，不再做中文对比实验**（原 §10 第9项已划掉）。
- **提示词位置**：官方智能体提示词在 `tradingagents\agents\` 各文件的 `system_message`；我们注入的 A 股上下文在 `context_patch.py`。
- **与 daily_stock_analysis (DSA) 的关系**（已比对，github.com/ZhuLinsen/daily_stock_analysis）：
  - 结论：**不替换，并用**。DSA 是"每日批量扫描 + 推送"系统（GitHub Actions 零成本部署、多渠道推送、回测、持仓），工程治理更严；TradingAgents 胜在多智能体对抗辩论的深度。
  - 定位：DSA 当"每日扫描器"，TradingAgents v2 当"深度研究台"——DSA 推送里异动的票再丢进 TradingAgents 跑辩论。
  - 可借鉴项（按价值）：① 结构化 JSON 输出（已在 v2 实现决策卡）；② 推送企业微信/飞书（用户暂不需要）；③ efinance 加入降级链（已评估，不引入）。

---

## 7. 已知问题 / 注意事项

- **挂载截断**：在沙盒里编辑 `ashare_vendor` 大文件时反复出现挂载滞后/截断。规避写法：用 heredoc 直接全量写入文件，不要用增量 Edit 依赖挂载同步。
- 残留报错多为**无害噪音**：akshare 新闻正则 bug → 东财直连已兜底；东财估值 502 → Tushare 估值已先成功。公司名和数据正常即说明主链路通。
- 财联社快讯偶尔超时走备用链，正常。

---

## 8. 待办 / 下一步（可选）

- [ ] 实测金十 MCP 在真实 key 下的快讯/搜索/日历（此前仅 mock 验证）。
- [ ] 观察 Tushare 中转站 7 天体验到期后是否自动回退；满意则续费。
- [ ] 若需要：把 DSA 的每日扫描 + 推送作为上游接入（当前用户未要求推送）。
- [x] ~~可选实验：`context_patch.py` 注入文本中英文对比~~ 🚫已划掉（2026-06-22 用户拍板永久保持英文推理，见 §6）。

### 改进路线图（2026-06-12 记录，按用户优先级）

1. **基准对比可选板块指数**：A股个股结算/alpha 基准除上证/深成外，增加「创业板指(399006)/科创50(000688)/所属板块指数」选项，并在 web 工作台加开关按钮选择基准。（数据已通：`market_data.get_close_series` 已支持指数；`_resolve_benchmark` 目前用 `benchmark_ticker` 固定 000001.SS，需改成可配置/按个股板块自动选。）
2. ~~**日线图叠加技术指标**~~ ✅**已完成**（2026-06-22）：web 工作台 K 线图增加均线（10EMA/50/200SMA）+ MACD/RSI/布林/VWMA 叠加显示。详见 §16。
3. ~~**剔除休市空档**~~ ✅**已完成**（2026-06-22）：日线图用缺失日期列表、分钟图用周末/隔夜/午休 bounds 剔除非交易时段空档（plotly rangebreaks）。详见 §16。~~并核对均线/指标计算是否已正确按交易日剔除休市日~~ → **✅ 2026-06-12 已复核：指标计算正确，无需改动**。四个A股数据源(Tushare/AKShare/通达信/pytdx)日线本身只含交易日、不补休市行；全链路无 `reindex/asfreq/date_range` 对齐日历；`stockstats.wrap` 逐行算，故 50_sma 等=最近 N 个**交易日**窗口，未被休市日稀释。美股侧 `_clean_dataframe` 的 ffill/bfill 只填行内 NaN、不新增日历行。`resample`(reader.py)仅周/月线用、尾部 dropna 去空周期、不在日线指标路径上。
4. **分钟线短线支撑**：接入 1 分钟 / 5 分钟线数据（通达信本地或 Tushare），为短线交易提供日内结构分析。
5. ~~**多周期 K 线 + 指标**~~ ✅**已完成**（2026-06-22）：web 工作台支持切换不同周期（日/周/月/60分/30分/15分）K 线及对应技术指标显示。详见 §16。
6. **美股数据源**：解决 yfinance 限流/反爬——评估 Alpha Vantage（已内置 vendor，需 key）或其他源；同时把结算层 `_fetch_returns` 的非A股分支从"暂时跳过"恢复为可用源。
7. **web 历史记录查看 + 5日结果回看**：在 web 工作台加「历史记录」视图，读 `~/.tradingagents/memory/trading_memory.md`（决策+反思）和 `~/.tradingagents/logs/<代码>/`（完整快照），列出每只票的历次分析；对已结算的记录展示「决策 5 个交易日后的实际收益 / 相对基准 alpha / 反思结论」，形成决策回看与命中率追踪。（数据来源即学习闭环产物，见 §9。）
8. **本地资料夹注入 + 单次分析记忆**（2026-06-12 新增）：让分析师读取本地文件夹中某只个股或宏观环境的资料（研报 PDF / 年报 / 政策文件 / 自有笔记，支持 txt/md/pdf/docx），把内容摘要后注入对应智能体上下文；读取后把摘要记录进「当次分析」，并按文件哈希缓存，**下次分析不再重复读取/重新摘要**（除非文件变更）。约定目录如 `~/.tradingagents/library/<代码>/` 与 `~/.tradingagents/library/_macro/`。复用现有 `context_patch.py`/`agent_utils` 注入路径；需做分块/摘要做 token 控制。**纯本地、无外部依赖，并为第 9 条提供通用「外部资料→摘要→注入→去重缓存」管线。**
9. **接入腾讯 ima / 共享知识库（RAG 检索）**（2026-06-12 新增）：让分析师按个股名/宏观主题检索一个大型共享知识库，取 top-k 片段注入分析。两条可行路径（需先做可行性 spike 二选一）：① **WeKnora 自建**（推荐起点）——腾讯开源的 ima 同源 RAG 框架，提供完整 9 类知识库 API、可本地部署、支持 PDF/Word/MD/OCR，最贴合「薄外挂层」哲学且不受云端 ToS/限流约束；② **ima Skills/MCP**——ima 已于 2026-03 上线 Skills 开放接口（笔记 skill 已 GA，**知识库 skill 计划近期推出**，待 GA 后可直连云端共享库）。检索结果走第 8 条的同一注入/token 控制/单次记录管线。

---

## 9. 本次会话变更日志（2026-06-12）

> 全部已实测验证。代码改动需**重启 Streamlit** 才加载（被 import 的模块；刷新页面无效）。

**配置**
- `.env` 补全 `TUSHARE_TOKEN` / `TUSHARE_HTTP_URL` / `JIN10_API_KEY`（用户已填真实值，`ts_relay.preferred()=True`）。venv 已装 `tushare`。
- A股行情改**本地优先**：`.env` 设 `ASHARE_PRICE_CHAIN=tdx,pytdx,tushare,akshare`（覆盖"配了中转站就 Tushare 优先"的默认），`TDX_MAX_STALE_DAYS=1`。注：该链只管 OHLCV；基本面/估值/财报等恒为 Tushare 优先。
- 清理了 `.env` 里 AKShare 相关过时注释。

**Bug 修复**
- `ts_relay.daily_basic`：中转站把 `trade_date` 混成 int/str 导致 `sort_values` 崩 → 排序前 `astype(str)`。（这是之前估值拉不到的真凶）
- `jin10._parse_body`：SSE 只取最后一行 `data:` 截断 JSON → 改为按事件分组拼接多行。
- `decision_card`：`max_tokens` 900→1500，修中文 JSON 截断。
- `web_app.py`：结果只存局部变量、点下载按钮 rerun 后页面清空 → 改存 `st.session_state`，渲染与 `run_btn` 解耦。

**辩论文体约束（验证有效，保留）**
- 在 `agents/utils/agent_utils.py:get_language_instruction()` 末尾追加机构研报文体指令（数字打头、禁演讲腔/呼告）。这是改了一处**官方文件**（无法 monkeypatch，各 agent import 时已绑定函数对象），上游合并时注意保留。旧的 `context_patch.py` 弱注入已移除。

**A股结算改造（学习闭环不再依赖雅虎）**
- 新增 `ashare_vendor/market_data.py:get_close_series()`：个股走本地/Tushare 降级链，指数 benchmark（000001.SS/399001.SZ）走 Tushare `index_daily`。
- `graph/trading_graph.py:_fetch_returns()` 改用它算 raw/alpha；**非A股代码暂时跳过、不回退 yfinance**（用户要求暂不用雅虎）。副作用：APH 等美股 pending 记录在此期间不会结算，待路线图第 6 条恢复美股源后补上。

**本地资料夹注入（§10 第3项，照 §11 实现，2026-06-12，Fable 5）**
- 新增 `ashare_vendor/library.py`（~430 行）：三级缓存判新（(size,mtime) 快路径不开文件 → sha1 兜底改名/touch → 真摘要）、pdf/docx/md/txt 抽取、DeepSeek flash 结构化摘要（theme/common_points/by_stock/mentioned_names）、简称→代码反查表（tushare 全表→akshare 兜底，`.name2code.json` 月级缓存）、多股报告按当前个股切片注入、单文件/总预算 token 控制、`Material`+`summarize_material()` 为 #9 预留。
- `context_patch.py` wrapper 末尾追加 library 注入（A股/非A股都生效；注入进 instrument_context 即随 state 存快照=「记录到当次分析」）。
- 新增 `tests/test_library.py`（7 用例全过，离线打桩）：覆盖 §11 三个校验点——①未命中简称静默不注入+强绑定文件夹兜底 ②mtime/sha1 双层缓存失效 ③多股切片不串味；另测 _macro 恒注入、美股文件夹、开关、LLM失败不缓存。集成冒烟过：无密钥 fail-open、base 不被污染、幂等。
- venv 新装 `pypdf` / `python-docx` / `pytest`。资料夹骨架已建：`~/.tradingagents/library/`（含 `_macro/`）。
- ⚠️ 待真实验证（代码已 fail-open 兜底）：中转站 `pro().stock_basic(fields="ts_code,name")` 全表拉取是否可用（不行会自动落到 akshare）；首次真实 PDF 摘要质量。

**ima 共享知识库接入（§10 第7项，B方案=发现层，2026-06-12，Opus 4.8）**
- 背景决策：ima OpenAPI **无全文获取接口**（核对官方 api.md/SKILL.md 全部 9 接口，唯一带正文的是 search_knowledge 的 highlight_content 高亮片段）。故采 B 方案：API 只做发现（按标的名/行业搜共享库出标题+片段），全文仍靠用户在 ima 客户端「提取文字」手动落地 library/。详见 §12 能力边界。
- 新增 `ashare_vendor/ima_kb.py`（~210行，仿 jin10 直连）：`configured()` / `resolve_kb_ids()`（IMA_KB_IDS 指定或自动列全部可见库）/ `search_knowledge()`（游标翻页+max_items 截断）/ `discover()`（跨库跨词去重、合并命中词、保留更长片段）/ `write_to_library()`（勾选项写 `library/<code>/ima_search_<ts>.md`，标注"非全文片段"）/ `resolve_search_terms()`（复用 context_patch 身份解析出 简称+行业 作默认检索词）。HTTP 强制 `proxies=None`；`register.py` 的 no_proxy 加 `ima.qq.com`。
- `web_app.py` 分析 tab 加 `render_ima_presearch()`：开始分析前折叠面板——解析名称/行业→预填检索词→「检索 ima」→ `st.data_editor` 勾选→「加入 library」。未配置时显示配置指引。**写入 library 后无需重启**，下次点分析实时扫描读取。
- 新增 `tests/test_ima_kb.py`（8 用例全过）：configured 开关、翻页截断、跨库跨词去重合并、resolve_kb_ids、write 落盘格式与目录、未配置 fail-open。
- `.env` 新增（均可空=休眠）：`IMA_OPENAPI_CLIENTID` / `IMA_OPENAPI_APIKEY` / `IMA_KB_IDS`。Key 从 https://ima.qq.com/agent-interface 获取。

**ima 真实 key 实测修正（2026-06-12，Opus 4.8）—— 官方 api.md 多处与实际不符，已按实测改：**
- **响应字段**：实际是 `{code, msg, data}`，**非手册的 `{retcode, errmsg}`** → `_post` 原检查 retcode 永远 None、把错误当成功静默吞掉。已改读 `code`。
- **`search_knowledge_base`**：limit 上限实测 **20**（手册写 50，传 50 报 `code:51 invalid ... Limit`）；条目字段是 **`kb_id`/`kb_name`**（非手册的 `id`/`name`）。已夹 limit≤20 + 兼容两套字段。（注：`get_knowledge_base` 那个接口确实用 `id`/`name`，各接口不统一。）
- **共享库可达性 = 可达**：auto-list 返回 个人库 + **共享库** + 订阅库；`get_knowledge_list`/`get_knowledge_base`/`search_knowledge` 在共享库上全 code=0 成功。原 web 搜不到纯是上面两个 bug 导致一条库都没列到。
- 🔴 **关键能力实测：共享库（普通成员）`search_knowledge` 的 `highlight_content` 恒为空** → **只有标题可用，无片段、无全文**。且**检索偏标题/主题词命中**：主题词（金刚石/散热/光模块）命中很好，**个股名（黄河旋风）基本搜不到**。已据此把 `write_to_library` 改为写**标题线索清单**（媒体类型标签 + 命中词，明确标注"非内容"），web 文案改为"只返回标题、用主题词搜"，过滤文件夹(media_type=99)。
- 测试补到 11 个（新增 `_post` 解析 code、list_kb 真实字段+limit 夹取、search 过滤文件夹）——之前漏测的正是字段解析层，故 bug 溜过。
- ⚠️ 仍待观察：频控阈值（110021）。结论：本功能只能当**标题发现器**（告诉你 ima 库里该主题有哪些研报），真正全文仍须用户在 ima 客户端「提取文字」另存 library。

**最终裁定（2026-06-12 用户拍板）：ima web 入口禁用。** 标题-only 注入对分析无实质帮助且有标题党污染风险；用户在 ima 客户端手动搜代码/名称更快、可全文搜。处置：
- `web_app.py` 移除预检索面板，原位置换成 **library 资料状态面板** `render_library_status()`：输入代码后列出相关资料（根目录/_macro/该代码文件夹），区分 ✅已读取(摘要已缓存)/🆕新发现(下次分析读取)/🔄已修改(将重读)，已读取的标注「是否会注入本股」+资料日期+主题。配套 `library.inspect_library()` 纯只读巡检（不调 LLM、不写缓存），测试已覆盖（19/19 过）。
- `ashare_vendor/ima_kb.py` + `tests/test_ima_kb.py` + `.env` 配置**留存休眠**（无 web 调用方），未来要用再接。
- 工作流定型：**ima 客户端搜→「提取文字」→ 存 `library/<代码>/` → web 面板看到「新发现」→ 点分析自动摘要注入。**

---

## 10. 执行序列（带模型标注，2026-06-12）

> 目标：**提高整体分析效果**。下面把 §8 路线图(1–9) + §8 顶部待办重排，按「对分析质量的杠杆」分层，并为每项指定模型与思考强度。
>
> **模型梯队（2026-06-12 核实并纠正旧版误判）**：能力 **Fable 5 ≥ Opus 4.8 > Sonnet 4.6 > Haiku 4.5**。Fable 5 是 Anthropic 当前**最强且最贵**（$10/$50）的通用模型（Mythos 级、SOTA 软件工程、自主运行时长最长，其安全兜底甚至回退到 Opus 4.8）——**不是"快而省"的档位**。旧版 §10 把 Fable 5 当廉价机械档用，是错的，已纠正。
>
> 分配原则（按「任务难度 × 赌注 × 成本」）：
> - **Haiku 4.5（低）**：单文件、低 blast-radius、错了一眼看出的机械改动。
> - **Sonnet 4.6（中）**：默认主力——需读懂既有代码、照规格落地的中等任务。
> - **Opus 4.8（高/极高）**：架构设计、选型判断、正确性核查。
> - **Fable 5（premium，省着用）**：只留给最难/最开放、且想**一次过不返工**的硬骨头（如美股反爬调试）；或愿为关键模块多花钱免返工（如 library.py）。日常琐碎别用它——等于用旗舰价改一行 plotly。

| 序 | 待办 | 模型 | 思考强度 | 理由 |
|---|---|---|---|---|
| ~~1~~ | ~~复核技术指标按交易日剔休市~~ ✅**已完成** | Opus 4.8 | 高 | 指标正确、无需改动（详见 §8 第3条） |
| ~~2~~ | ~~本地资料夹注入——架构设计~~ ✅**已完成** | Opus 4.8 | 极高 | 设计定稿见 §11 |
| ~~3~~ | ~~本地资料夹注入——落地编码~~ ✅**已完成**（2026-06-12，Fable 5） | Fable 5 | 中 | `library.py` + 测试 7/7 通过，详见 §9 变更日志 |
| ~~4~~ | ~~历史记录 + 5日回看视图~~ ✅**已完成**（2026-06-12，Sonnet 4.6） | Sonnet 4.6 | 中 | `web_app.py` 顶层加「历史记录」tab，读 TradingMemoryLog + snapshot JSON |
| ~~5~~ | ~~基准可选板块指数 + web 开关~~ ✅**已完成**（2026-06-12，Sonnet 4.6） | Sonnet 4.6 | 中 | `web_app.py` 加 benchmark 选择器 + `resolve_benchmark_for_ui()` 板块感知；同时修复裸六位代码落到 SPY 的隐性 bug |
| ~~6~~ | ~~ima/共享知识库——可行性 spike~~ ✅**已完成**（2026-06-12，Fable 5） | Fable 5 | 高 | **结论：直连 ima OpenAPI，不引入 WeKnora**。知识库 skill 已 GA，官方 API 规格已下载实测（存 `ashare_vendor/ima_kb_api.md`），详见 §12 |
| ~~7~~ | ~~ima 知识库接入~~ 🚫**web 入口已按用户决定禁用**（2026-06-12，Fable 5） | — | — | 实测后标题-only 价值不足，用户改为 ima 客户端手动搜+提取全文。`ima_kb.py` 留存休眠；web 换成 **library 资料状态面板**（已读取/新发现/将重读 + 是否注入本股） |
| ~~8~~ | ~~分钟线短线支撑~~ ✅**已完成**（2026-06-12，Opus 4.8 设计 + Sonnet 4.6 实现） | Opus 4.8 → Sonnet 4.6 | 高→中 | 设计见 §13，实现见 §14。新增 `ashare_vendor/intraday.py` + 工具接线；7/7 测试通过 |
| ~~9~~ | ~~context_patch 中英文推理 A/B 实验~~ 🚫**已划掉**（2026-06-22 用户拍板） | — | — | 用户决定**永久保持英文推理**，不再做对比实验，§6 决策定型 |
| ~~10~~ | ~~日线图叠加技术指标~~ ✅**已完成**（2026-06-22，Sonnet 4.6） | Sonnet 4.6 | 中 | EMA10/SMA50/SMA200 + BOLL(20,2) + VWMA(20) + MACD(12,26,9) + RSI(14)，详见 §16 |
| ~~11~~ | ~~剔除休市空档·画图部分 rangebreaks~~ ✅**已完成**（2026-06-22，Sonnet 4.6） | Sonnet 4.6 | 中 | 日线缺失日期列表 + 分钟图周末/隔夜/午休 bounds，详见 §16 |
| ~~12~~ | ~~多周期 K 线 + 指标切换~~ ✅**已完成**（2026-06-22，Sonnet 4.6） | Sonnet 4.6 | 中 | 与10/11合并实现：日/周/月/60分/30分/15分周期选择器，详见 §16 |
| ~~13~~ | ~~Tushare 到期回退监控~~ 🚫**已划掉**（2026-06-22 用户拍板） | — | — | 不再单列待办，到期前手动查 https://ts.gyzcloud.top/key 即可 |
| 末 | **美股数据源**（原路线图6，**用户暂不需要，置于最末**） | **Fable 5**（或 Opus 4.8 省成本） | 高 | 反爬/限流是最 gnarly 的开放式调试，最吃 Fable 5 的长自主 + SOTA SWE；不想花旗舰价就 Opus |
| 删除 | DSA 每日扫描+推送（原§8待办） | — | — | 用户已确认不需要 |

> 已完成（不再列入）：
> - Tushare 是否在跑、金十 MCP 真实 key 实测 —— 2026-06-12 已验证。
> - ✅ **第1项**（技术指标按交易日剔休市）2026-06-12 复核：指标正确、无需改动，剩余仅显示层 rangebreaks（第11项）。详见 §8 第3条。
> - ✅ **第2项**（本地资料夹注入·架构设计）2026-06-12 定稿，见 §11。下一步第3项编码。

---

## 11. 本地资料夹注入 — 架构设计定稿（§10 第2项，2026-06-12，供第3项 Sonnet 照此编码）

> 目标：分析师读取本地 `library/` 下的个股/宏观资料（研报/年报/纪要/笔记，txt/md/pdf/docx），摘要后注入分析；文件未变则走缓存不再读取。决策：语义=A（缓存复用、每次注入相关切片）；粒度=v1（共享 instrument_context + 标签）。

### 注入 seam（唯一改一处）
全流程仅 `propagate()` 启动时调一次 `resolve_instrument_context(ticker)`（trading_graph.py:367），返回的 `instrument_context` 喂所有 agent 且随 state 存进快照 `results_dir/<ticker>/.../full_states_log_<date>.json`——**注入与"记录到当次分析"同一字符串搞定**。在 `context_patch.apply_context_patch` 的 wrapper 末尾市场无关地追加：
```python
base = build_ashare_context(ticker) if (ok and asset_type=="stock") else orig(...)
extra = library.build_library_context(ticker)   # 新模块，fail-open，无资料返回 ""
return base + (f"\n\n{extra}" if extra else "")
```

### 目录与缓存
```
~/.tradingagents/library/
  <code>/      个股强绑定资料（可选）
  _macro/      宏观/政策，永远注入
  *.*          根目录：丢任意报告，按自动抽取的"提及个股"路由
  .cache/<sha1>.json   结构化摘要缓存（一份/文件版本）
  .index.json          path -> {size,mtime,sha1}
  .name2code.json      公司简称->代码 反查表（akshare stock_info_a_code_name / tushare 全表，缓存）
```

### 管线 build_library_context(ticker)
1. **不再读取的快路径**：`(size,mtime)` 对上 `.index.json` → 不开文件，直取 `.cache` 摘要。
2. **改名兜底**：变了才读字节算 `sha1`；`.cache/<sha1>.json` 在 → 复用。
3. **慢路径（仅新增/改动）**：抽正文（pdf→pypdf，docx→python-docx，md/txt 直读）→ 超长分块 → DeepSeek flash（复用 decision_card 客户端：deepseek-v4-flash, temp 0.1, json, fail-open）产**结构化摘要**：
```json
{"theme":"…","common_points":["主题级要点"],
 "by_stock":{"四方达":["该票催化剂…"]},
 "mentioned_names":["四方达",…],"native_date":"2026-06-11"}
```
   → 用 `.name2code.json` 把 mentioned_names 映射成代码存进缓存条目的 `mentioned_codes`。

### 注入切片（解决 token + 自动路由）
分析代码 C 时，对每份资料注入 = `theme + common_points + by_stock[name(C)]`（C∈mentioned_codes 或在 `<C>/` 文件夹才算命中；`_macro/` 全注入 common_points）。多只票的行业报告**只注入当前票那几条**，不串味。
- 预算：缓存摘要 cap ~400 token（`LIBRARY_PER_FILE_TOKENS`）；单次分析注入总额 cap ~2500（`LIBRARY_MAX_TOKENS`），超额按「`<code>/`强绑定 > 自动命中 > macro」+ 新优先 截断。
- 摘要用**英文要点**（与 context_patch 英文注入一致，agent 英文推理更稳）；每条带 `[基本面]/[政策]/[技术]/[消息]` 标签与文件名+native_date provenance。
- 防陈旧头："用户预置参考资料，可能早于分析日；与实时工具数据冲突时以实时为准，除非资料更新或更权威（如政策原文）。"

### 失败兜底 / 依赖 / 配置
- 每步 try/except，缺解析库/LLM 超时/文件损坏 → 跳过该文件、log warning、绝不阻塞分析。
- 新依赖：`pypdf`（**venv 已装**）、`python-docx`（待装）。
- `.env`：`LIBRARY_ENABLED`(默认 on)、`LIBRARY_DIR`(默认 ~/.tradingagents/library)、`LIBRARY_PER_FILE_TOKENS`、`LIBRARY_MAX_TOKENS`、`LIBRARY_SUMMARY_MODEL`。

### 为 #9（ima/知识库）预留
模块内部以 `Material(source,id,title,text_or_path,native_date)` 为单位；本地文件是一种 source，#9 的 ima/WeKnora 检索结果产同样 Material，复用同一条 摘要→缓存→预算→注入→provenance 管线。

---

## 12. ima 共享知识库接入 — 可行性 spike 定稿（§10 第6项，2026-06-12，供第7项 Sonnet 照此编码）

### Spike 结论：直连 ima OpenAPI；WeKnora 评估后不引入

**状态核实**：ima 知识库 Skill 已于近期 GA（3月时还是"计划推出"）。所谓 skill 本质是**官方 OpenAPI + API Key**——下载官方包 `ima-skills-1.1.2.zip` 拆解确认，不必是"龙虾"类产品，任何 HTTP 客户端可直连。完整 API 规格已存仓库：`ashare_vendor/ima_kb_api.md`。

**关键发现（推翻原"二选一"框架）**：WeKnora 和 ima OpenAPI 不是同一目标的两条路——
- **WeKnora** = 自建本地 RAG（Docker+embedding 模型+MinIO），**访问不到 ima 云端共享知识库**，资料须手工导出重灌（共享库内容多为他人添加、无导出 API，迁移基本不可行）。且"本地自有资料注入"已被 §11 第8项覆盖，引入 WeKnora 功能重叠、违背薄外挂哲学。→ **不引入**（同 efinance，评估后弃）。
- **ima OpenAPI** = 唯一能直达用户已加入的云端共享知识库的路径，零部署。→ **采用**。

### API 速览（详见 ima_kb_api.md）
- Base: `POST https://ima.qq.com/openapi/wiki/v1/<endpoint>`，Header：`ima-openapi-clientid` + `ima-openapi-apikey`（从 https://ima.qq.com/agent-interface 获取）。
- 核心检索：`search_knowledge {query, knowledge_base_id, cursor}` → `[{media_id, title, parent_folder_id, highlight_content}]`（游标翻页）。
- 辅助：`search_knowledge_base`（按名找库 id）、`get_knowledge_list`（浏览目录）、`get_knowledge_base`（库信息含推荐问题）。
- 响应统一 `{retcode, errmsg, data}`；`110021`=频控需退避，`110030`=无权限。

### 能力边界（2026-06-12 复核 api.md + SKILL.md 全部 9 接口确认，必须如实告知 agent）
> ⚠️ **核心限制：API 拿不到全文。** 客户端 ima copilot 的「提取PDF文字/提取图片文字/全量阅读理解」是客户端内大模型直连后端的能力，**未以 OpenAPI 开放**。9 个接口只有 写入/管理、浏览(标题)、检索(片段) 三类，**没有任何 media_id→全文/OCR 的接口**（连"添加笔记"都标注"无需下载内容"）。
1. **关键词搜索，非语义 RAG**——快讯式命中，不是向量检索。
2. **唯一带正文的字段 = `search_knowledge` 的 `highlight_content`，是关键词命中处的高亮**片段**，非全文**。读整篇只能人去 ima 客户端。
3. **PDF vs 图片覆盖不均**（据 ima 自述）：PDF 正文全文索引→标的名即使不在标题也能命中片段；**图片只索引附带「概要」文本、不做像素 OCR**→只在图里出现的标的（如截图底部"关联个股"）搜不到、取不回。
4. provenance 须标明"来自 ima 检索片段（仅摘录，非全文）"，命中标题在报告里列出供用户回 ima 客户端读全文。

### ⚠️ 因上述边界，第7项定位已调整（待用户拍板 A/B/C）
原 §12 假设"检索片段注入"有足够价值，但全文不可得使其降级为**线索+片段**，非全文 RAG。三选一：
- **A 纯片段注入**：搜标的名→title+highlight 注入。PDF 命中可用，图片弱；风险=agent 拿残片当全貌。
- **B（推荐）发现层+本地库全文**：ima API 只做**发现**（共享库里哪些资料提到该票→出标题清单），高价值篇目用户在 ima 客户端「提取文字」导出→丢进 §11 本地 `library/`，由已建好的全文管线注入。ima 管发现、本地库管全文，各取所长、绕开全文短板。
- **C 暂缓**：全文已被 §11 覆盖，可先不接 ima。
> 用户倾向确定前，第7项实现规格（下）按"能搜则注入片段"写，B 方案只是少注入、多在 UI 列标题清单，改动小。

### 第7项实现规格
- 新增 `ashare_vendor/ima_kb.py`（~150行，仿 jin10.py 直连模式）：
  - `configured()`：`IMA_OPENAPI_CLIENTID`/`IMA_OPENAPI_APIKEY` 都非空。
  - `search(query, kb_id, max_items=8)`：POST search_knowledge，游标翻页取前 N，110021 退避一次，fail-open 返 []。
  - `build_ima_materials(ticker)`：对 `IMA_KB_IDS`（逗号分隔的库 id，.env 配置）逐库检索。query 用「公司简称」（复用 context_patch 身份解析结果）+「行业关键词」；宏观库可配 `IMA_KB_MACRO_IDS` 用固定宏观 query 集。命中片段拼成文本构造 `library.Material(source="ima", id=media_id, title=..., text=拼接的 highlight 片段)` → `summarize_material()` 进 §11 缓存管线（片段短时可跳过 LLM 摘要直接注入，按 token 阈值判断）。
  - 注入挂点：`library.build_library_context()` 末尾追加 ima 块（或 context_patch 并列调用），同一预算池。
- `.env` 新增：`IMA_OPENAPI_CLIENTID` / `IMA_OPENAPI_APIKEY` / `IMA_KB_IDS` / `IMA_KB_MACRO_IDS`（均可空=功能休眠）。
- **首次真实验证清单**：① key 能否搜到已加入的共享库（API 权限模型未实测）；② highlight_content 的信息量是否值得注入；③ 频控阈值。

### 新增/改动清单（第3项 Sonnet 实施）
- 新增 `ashare_vendor/library.py`（≈250 行：抽取/缓存/flash摘要/名称映射/切片注入/预算）。
- `context_patch.py` 加注入两行（见上）。
- 名称→代码反查表构建（akshare/tushare 全表，缓存到 `.name2code.json`）。
- 可选：`web_app.py` 报告里列「本次引用的本地资料」+ 侧栏预览（可推迟）。
- **校验点（错了不显眼，需测）**：① 简称未命中名称表 → 报告静默不注入；② 缓存失效 mtime/sha1 双层；③ 多股报告切片只注入当前票。

---

## 13. 分钟线短线支撑 — 日内结构设计定稿（§10 第8项，2026-06-12，Opus 4.8 设计，供 Sonnet 取数接线）

> 目标：用分钟线给「短线交易」提供**日内微观结构**——精确的成交密集支撑/压力位、VWAP 持仓成本位、近期日内多空性格（吸筹/派发）、A股涨跌停盘口动态——锐化决策卡的入场区/止损/目标，且尊重 T+1。这是日线看不见的**新维度**。

### ⚠️ 关键发现：取数层已存在，本项实际工作量小于预估
`tdx_local/reader.py` **已完整支持分钟线**：`read_minute(symbol, freq="5min"/"1min")` 解析本地 vipdoc `.lc1/.lc5`；`get_bars_online(period="5min"/"1min")` 走 pytdx（category 0=5min, 8=1min, `max_bars=2400`）；`get_bars(period=...)` 统一入口。§10 表里"取数机械"已被验证——**无需从零写 fetcher**。真正要做的是：① `market_data` 级降级包装；② **日内结构特征计算层**（Opus 设计的核心）；③ 注入接线 + 提示词。

### 设计裁定
- **频率/窗口**：默认 **5分钟 × 最近 10 个交易日**（≈480 bar，token 友好、足够定 S/R 与性格）。`.env` `INTRADAY_FREQ=5min`(可 1min)、`INTRADAY_LOOKBACK_DAYS=10`。
- **价格口径必须「不复权」**：涨跌停价、成交密集区都基于**原始价**；tdx 分钟天然不复权——正确。**严禁喂 qfq 复权分钟线**（会让 limit-price 检测全错）。Sonnet 注意：复用 daily 链时别套用 ASHARE_ADJUST。
- **特征确定性计算（纯 pandas，不调 LLM）**：与 library.py 不同，日内特征是**数值计算**不是文本摘要——直接算、便宜、可靠、可测。
- **注入 seam = 工具（market_analyst）**，非全局 context 注入。理由：① 日内微观结构是**技术/择时**信号，归市场分析师域（同 `get_verified_market_snapshot`）；② 让它落进 `market_report` → 自然流向多空/研究经理/交易员/风控 + **决策卡抽取入场/止损/目标**；③ 不污染基本面/新闻 agent 的 context；④ 契合"价格断言须来自工具输出"的现有护栏（工具=可信源，agent 可放心引用其点位）。代价：改 1 个官方文件 `market_analyst.py`（system_message + tools），**须标记上游合并保留**（同 §9 的 get_language_instruction 先例）。

### 特征集 v1（每项一行输出，无则省略该行，控制 token）
价格符号：窗口最后一根 5min close = 现价 P；prev_close 从分钟帧自取（前一交易日最后一根 close）。

1. **成交量分布支撑/压力（headline，对应"支撑"）**：把窗口价域分 ~30 桶，按 bar 成交量累加 → **POC**(成交最密价=最强磁吸/支撑)、**价值区** VAH/VAL(POC 邻域累计 ~70% 量)。现价**下方**最近高量节点=支撑，**上方**=压力。输出 POC、VA 区间、最近支撑/压力位 + 距现价 %。
2. **VWAP 持仓成本位**：窗口 VWAP=Σ(typical×vol)/Σvol，typical=(H+L+C)/3。统计近 10 日**收盘站上当日 VWAP 的天数**。输出 VWAP、现价偏离 %、站上天数（站上=持有者浮盈、回调易被接；跌破=套牢盘压顶）。
3. **日内性格（近 5 日，对应"短线"）**：每日 close-in-range=(C−L)/(H−L)；尾盘 30min 净方向(sign(close−open))与量占比；上午 vs 下午涨幅。聚合**吸筹/派发/中性**裁决（close-in-range 中位 + 尾盘偏向）。输出裁决 + 支撑计数。
4. **A股涨跌停盘口（A股杀手锏，仅当窗口内有触板才输出）**：板块涨跌幅由代码前缀定(688/689,300/301→20%；北交所 30%；主板 10%；名称含 ST/*ST→5%)；limit_price=round(prev_close×(1±pct),2)。每日：日内高是否触涨停(±1 tick)？收盘是否封死？**开板次数**=日内"在板→脱板"跳变数；封板时间=首次封死的分钟。输出："近10日 N 日触涨停；最近一次<日期>封板 HH:MM、开板 K 次、收盘[封死/炸板]"。强弱：早封+0 开板=强；多次开板=派发。
5. **日内波动·止损参考（一行）**：窗口内 (H−L)/prev_close 的中位数=日内振幅；提示"T+1 当日不可卖，止损窄于 ~1 个日内振幅(¥Y)易被噪音扫损"。

### 工具输出格式（英文，~200–300 token，与英文推理一致）
```
## Intraday Microstructure (last 10 trading days, 5-min bars; source: <TDX-local/pytdx>; raw/unadjusted prices)
Current price ¥P (last bar <datetime>).
- Volume-profile: POC ¥X (heaviest traded); value area ¥VAL–¥VAH. Nearest support ¥S (HVN, M% of window vol) ~A% below price; nearest resistance ¥R ~B% above.
- VWAP: 10-day ¥V; price +Z% vs VWAP; closed above daily VWAP on 7/10 days (holders in profit, dips likely bought).
- Intraday character (last 5 sessions): median close-in-range 0.78; final-30-min net buying 4/5 days → accumulation.
- Limit board: touched +10% limit on 2/10 days; last <date> sealed 13:42, opened 0×, closed sealed (strong follow-through odds).
- Intraday volatility: median daily swing ±3.1%; under T+1 a stop tighter than ~¥Y (1 swing) is likely noise-triggered.
These are intraday-verified levels; reconcile with daily indicators, do not override the daily trend.
```
- 数据不可得（无本地 vipdoc 且 pytdx 不通）→ 返回一行 `Intraday microstructure unavailable (no local TDX minute files and pytdx unreachable); proceeding on daily data only.` agent 略过该节，**fail-open 不阻塞**。
- 非A股 → 一行 `Intraday microstructure not available for non-A-share symbols.`
- 触板节(4) 无则整行省略；样本 <2 日 → 算可算的并注"thin sample"。

### 新增/改动清单（Sonnet 实施）
1. **新增 `ashare_vendor/intraday.py`**（核心，~250 行）：
   - `get_minute_df(symbol, freq, lookback_days)`：本地 `read_minute` → pytdx `get_bars_online` 降级（仿 `get_daily_df`，**不复权**），返回含 datetime 的帧或空。
   - `compute_intraday_structure(df, ...) -> dict`：纯计算 1–5 特征（无副作用、可单测）。
   - `get_intraday_structure(symbol, curr_date) -> str`：A股 gate + 取数 + 计算 + 格式化输出（fail-open）。涨跌停 pct 复用 `context_patch._board` 思路（或内联前缀判断 + ST 名称检测，名称取自 `context_patch._resolve_ashare_identity`）。
2. **新增 `tradingagents/agents/utils/intraday_structure_tools.py`**：`@tool get_intraday_structure(symbol, curr_date)` → 直调 `ashare_vendor.intraday.get_intraday_structure`（仿 `market_data_validation_tools.py` 直调模式，**不走 VENDOR_METHODS**，本功能纯A股）。
3. **`agent_utils.py`** 加一行 re-export。
4. **`market_analyst.py`**（官方文件，标记上游合并）：`tools` 列表加 `get_intraday_structure`；system_message 加**祈使指令**——
   > `For short-term entry/exit precision, you MUST call get_intraday_structure once for this ticker and date. Use its volume-profile POC/value-area and VWAP as intraday-verified support/resistance, fold its intraday character and limit-board read into your timing discussion, and let its intraday-volatility line inform stop placement under T+1. Treat its levels as tool-verified (same authority as get_verified_market_snapshot); reconcile with daily indicators rather than overriding the daily trend.`
5. **`.env`**：`INTRADAY_ENABLED`(默认 on)、`INTRADAY_FREQ=5min`、`INTRADAY_LOOKBACK_DAYS=10`。
6. **新增 `tests/test_intraday.py`**（离线，打桩 reader 返回合成 5min 帧）：① 已知量分布 → POC/VAH/VAL 正确；② VWAP 数学；③ close-in-range/吸筹裁决；④ 触板检测三态（封死/炸板/无触板）；⑤ 非A股→优雅提示、空帧→fail-open 一行。

### 校验点（错了不显眼，需测）
- ① **复权口径**：误用 qfq → limit_price 全错、触板检测失效。必须不复权。
- ② **prev_close 跨日**：limit 计算的 prev_close 必须是**前一交易日**收盘，非窗口首根；午休 11:30/13:00 不可当跨日。
- ③ **POC/价值区**在量价分布退化（单日、停牌、一字板全天封死无成交分布）时不 NaN/不崩，给"thin/degenerate"提示。
- ④ 触板 ±1 tick：A股 tick=0.01 元（科创板/部分=0.01 同），用绝对 0.01 容差而非百分比。

---

## 14. 第8项落地变更日志（2026-06-12，Sonnet 4.6）

> §13 设计（Opus 4.8）→ 本节实现（Sonnet 4.6）。所有文件已写入，测试 7/7 通过。重启 Streamlit 生效。

### 新增文件

**`ashare_vendor/intraday.py`**（~280 行，纯计算，无 LLM 调用）
- `get_minute_df(symbol, freq, lookback_days)`：本地 TDX → pytdx 降级，**不复权**，返回含 `datetime`+`date` 的 DataFrame
- 5 个纯计算子函数（模块级，可单测）：
  - `_compute_vol_profile(df, n_bins=30)`：POC、价值区 VAH/VAL、最近支撑/压力
  - `_compute_vwap(df, feature_dates)`：窗口 VWAP + 每日收盘站上率
  - `_compute_intraday_character(df, char_dates)`：close-in-range 中位 + 尾盘方向 → accumulation/distribution/neutral
  - `_compute_limit_board(df, feature_dates, prev_close_map, pct)`：触板天数、首次封板时间、开板次数、收盘状态；prev_close 取**前一交易日末根**（非窗口首根），±0.01 tick 容差
  - `_compute_intraday_volatility(df, feature_dates, prev_close_map)`：日内振幅中位数 + T+1 止损底线 ¥
- `compute_intraday_structure(df, code, company_name, lookback_days) -> dict`：组合入口，fail-open 每个子函数独立 try/except
- `_format_output(features, source, freq, lookback_days) -> str`：格式化为 ~250 token 英文段落
- `get_intraday_structure(symbol, curr_date) -> str`：主入口，A股 gate + 取数 + 计算 + 格式化；非A股/空帧/出错均返回一行提示不阻塞

**`tradingagents/agents/utils/intraday_structure_tools.py`**
- `@tool get_intraday_structure(symbol, curr_date)`：直调 ashare_vendor，仿 `market_data_validation_tools` 直调模式

**`tests/test_intraday.py`**（7 个测试，7/7 通过）
- `test_vol_profile_poc_and_sides`：POC 在高量区，support/resistance 在现价两侧
- `test_vwap_math`：Σ(typical×vol)/Σvol 精度 0.01
- `test_character_accumulation_and_distribution`：CIR+尾盘 → 裁决正确
- `test_limit_board_three_states`：封死/炸板/无触板 三态，prev_close 跨日正确
- `test_non_ashare_returns_graceful_message`：AAPL → "not available" 提示
- `test_empty_df_returns_graceful_message`：空帧 → "unavailable" 提示
- `test_disabled_returns_graceful_message`：开关关闭 → "disabled" 提示

### 改动官方文件（⚠️ 上游合并时保留）

**`tradingagents/agents/analysts/market_analyst.py`**
- `tools` 列表加 `get_intraday_structure`
- `system_message` 加祈使指令（见 §13 原文），位置在 `get_verified_market_snapshot` 指令之前

**`tradingagents/agents/utils/agent_utils.py`**
- 加一行 `from tradingagents.agents.utils.intraday_structure_tools import get_intraday_structure`

### .env 新增配置（注释默认值）
```
# INTRADAY_ENABLED=true          # 总开关，默认开
# INTRADAY_FREQ=5min             # 频率（5min/1min），默认 5min
# INTRADAY_LOOKBACK_DAYS=10      # 特征窗口交易日数，默认 10
```

### 本地数据覆盖范围核查（2026-06-12 实测）

| 周期 | 起始 | 截止 | 备注 |
|------|------|------|------|
| 日线 | **2008-01-02** | 2026-06-12 | 完整；算法检测到的"断裂"全是春节/国庆/2020疫情延假（>10自然日），不是真实缺口 |
| 5分钟线 | **2026-03-03** | 2026-06-12 | **70 交易日**；TDX 软件运行后才积累，历史数据无法补 |
| 1分钟线 | **2026-03-02** | 2026-06-12 | **71 交易日**；同上 |

**实际影响**：`INTRADAY_LOOKBACK_DAYS=10`（默认）完全覆盖；历史回测（分析几个月前的票）拿不到当时分钟线 → fail-open 一行提示，不阻塞日线分析。未来随 TDX 软件持续运行，分钟线历史会自动增长。

---

## 15. 本次会话变更日志（2026-06-22，Opus 4.8）

> 背景：项目搁置一段时间后重启。旧 Tushare 中转站 7 天套餐已过期，续费 30 天重新激活，并修了一批跑黄河旋风(600172)等票时暴露的问题。**所有改动都在外挂层(`ashare_vendor/`)和 `web_app.py`，没动官方框架文件。** 改动需重启 Streamlit 才加载。

### 配置
- `.env`：Tushare 续费 **30 天**，新 token `40c0...`（旧 `7879...` 已过期）。**到期约 2026-07-22**，余期查 https://ts.gyzcloud.top/key 。URL 未变。
- 过期症状很隐蔽：`ts_relay.configured()`/`preferred()` 仍返回 `True`，但实际调用 `stock_basic` 返回「空 DataFrame 且无列名」。判断真过期必须打一发真实 API。

### Bug 修复
1. **`ts_relay.py` 新增 `_patch_query()`**：绕开 tushare SDK 1.4.29 在 `DataApi.query()` 里 `kwargs.setdefault('ts_type_name', http_url)` 注入的多余参数。该参数让中转站的**宏观接口 cn_cpi/cn_gdp/cn_pmi/shibor 返回 0 行**（行情类接口不受影响）。修后 `ts_extras.macro_snapshot()` 出真数据（CPI/GDP/Shibor）。`pro()` 在设置中转 URL 后调用它替换实例 `query`。
2. **`ts_extras.py` repurchase**：`p.repurchase(...)` 补 `ts_code=tsc` 参数。中转站现要求必填，原市场级调用被静默拒、回购数据恒空。修后茅台返回 3 条回购记录。
3. **`decision_card.py`**：决策卡空响应（`Expecting value: line 1 column 1 (char 0)`）→ 改为空响应重试一次 + 新增 `_loads_lenient()`（`json.loads(strict=False)` 容忍字符串内裸控制字符 + 尾部截断退到最后一个 `}` 再试）。
4. **`library.py`**：摘要 JSON 解析改用同款 `_loads_lenient()`，修 ima 导出的 `.pdf.md` 报 `Unterminated string`（模型在 JSON 串里吐裸换行）导致摘要失败。
5. **`jin10.py`**：金十快讯 404 + 中文乱码两个 bug。(a) 任意 404 都清会话(`_SESSION["id"]=None`)并由 `call_tool` 重连重试一次（原逻辑只在已有 session 时重连，长任务里会话过期就崩）；(b) `_parse_body` 强制 `resp.encoding="utf-8"`（金十返 UTF-8 不带 charset，requests 按 latin-1 解码 → 中文乱码/解析空，这是之前金十"返回空"的真因）。修后 flash_list/search_flash/calendar 实测返回正常中文。金十真实工具名：`list_flash/search_flash/list_news/search_news/get_news/list_calendar/get_kline/get_quote`。

### Web 工作台 UI（`web_app.py`）
- 新增 `resolve_stock_name(code)`（`@st.cache_data`，复用 `context_patch._resolve_ashare_identity`）和 `code_with_name(code)`。分析页标题、K线图标题、历史列表每条、历史筛选下拉**全部显示「代码 + 中文名」**（如 `600172 黄河旋风`）；非A股(美股)无名则只显代码。
- `use_container_width`（8 处，已弃用刷屏）→ 新 API：`=True` 换 `width="stretch"`、`=False` 换 `width="content"`。

### 已查明但未修（按用户决定，非致命）
- **Yahoo 限流重试**：A股指标/行情/身份三条路都走本地不碰雅虎（已复现验证）。重试来自 `interface.py:route_to_vendor` 的 `ashare,yfinance` 链——ashare 对 LLM 传的某次具体参数返回空(`NoMarketDataError`)时落到 yfinance。fail-open 不影响结果。根治需改官方路由层让 A股不进 yfinance 兜底，blast radius 大，暂缓。
- **结构化输出失败**（Research Manager/Trader/Portfolio Manager 报 `'NoneType' object has no attribute 'recommendation/action/rating'`）：DeepSeek 的 `with_structured_output().invoke()` 返 `None`，官方 `agents/utils/structured.py` 退化成 `plain_llm.invoke` free text 自愈。报告照常产出，代价是这三个 agent 各白多打一次 LLM。根治需改官方 agent 工厂 `method=`，收益不确定，暂留。
- **ConnectionResetError / `_ProactorBasePipeTransport`**：Windows + Python 3.14 asyncio 套接字清理噪音（远端关 keep-alive），非致命、不崩。要消得装自定义 asyncio 异常处理器，糊墙不做。

### 记忆（`~/.claude` 与项目记忆库）
- 全局 `~/.claude/CLAUDE.md` 新建：只放交流风格段（从 Obsidian `SecondBrain/CLAUDE.md` 提炼，不含隐私）。原因：风格档在 Obsidian 仓库里、作为附加工作目录不会被自动加载，所以之前的 session 没生效。全局文件每个 session 都加载。
- 项目记忆库新增 3 条：中转站 token/到期、宏观+news+repurchase 坑、A股 yfinance 兜底路径。

---

## 16. 本次会话变更日志（2026-06-22 续，Sonnet 4.6）— K线图技术指标 + 多周期（§10 第10/11/12项）

> 用户拍板第9、13项划掉（§10 已更新），第10/11/12项合并实现（同一组 UI 控件、同一份数据管线）。新增文件均在外挂层/web层，未改官方框架。重启 Streamlit 生效。

### 新增 `ashare_vendor/chart_data.py`（~150行，纯计算+取数，无 LLM 调用）
- `get_period_df(symbol, period, end) -> (df, source)`：统一多周期入口。
  - `daily/weekly/monthly` 复用 `market_data.get_daily_df` 取日线，周/月线再过 `TdxLocalReader.resample`（窗口按周期拉长：日240天/周600天/月1500天，否则周期线太短看不出趋势）。
  - `60min/30min/15min` 仅 A股：调 `intraday.get_minute_df` 取本地/在线5分钟线（不复权）→ 按 `end` 截断防未来数据 → `_resample_from_5min` 聚合。
- `_resample_from_5min(df5, period)`：**分桶用"每日内顺序位置"而非时间戳对齐**——A股早晚两段(9:30-11:30/13:00-15:00)各天然 24 根5min bar，3/6/12 整除两段，故按 `groupby(date).cumcount() // n` 分桶不会跨午休拼错 bar（已用合成数据测试验证：60min 第2根收于11:30、第3根收于14:00，二者不连续）。
- `compute_indicators(df, selected)`：`{'ma','boll','vwma','macd','rsi'}` 子集 → 对应列（`ema10/sma50/sma200`、`boll_mid/ub/lb`、`vwma20`、`macd/macd_signal/macd_hist`、`rsi14`），纯 pandas 计算（EMA/SMA/滚动std/RSI用Wilder平滑），不依赖 stockstats（避免与官方 yfinance 路径耦合）。
- `daily_rangebreaks(dates) -> list`：算 `min~max` 完整日历日 减去 实际有数据的日期 = 缺失日期列表，喂给 plotly `rangebreaks=[dict(values=missing)]`（自动覆盖周末+节假日，比手写 `bounds=["sat","mon"]` 更准，因为还剔除了法定假日）。
- `minute_rangebreaks() -> list`：分钟图固定剔除周末 + 隔夜(15:00-次日9:30) + 午休(11:30-13:00)三段 `bounds`。

### `web_app.py` 改动
- `render_kline()` 重写：图表上方加两个控件——周期选择器(`CHART_PERIOD_OPTS`: 日/周/月/60分/30分/15分) + 指标多选(`CHART_INDICATOR_OPTS`: MA/BOLL/VWMA/MACD/RSI，默认勾 MA)。
- 用 `plotly.subplots.make_subplots` 按是否勾选 MACD/RSI 动态出 1~3 行子图（行情+可选MACD行+可选RSI行，`shared_xaxes=True`），MA/BOLL/VWMA 叠加在主图，rangebreaks 按周期类型选日线版/分钟版应用到全部 x 轴。
- `load_kline` 改名 `load_period_kline`，缓存键加 `period`（`@st.cache_data(ttl=600)`）。
- 移除未用的 `timedelta` import（窗口计算移进 `chart_data.py`）。

### 新增 `tests/test_chart_data.py`（12 用例全过，离线，无网络/无本地vipdoc依赖）
- 分钟聚合 3 例：60min跨午休不拼错（断言相邻bucket时间不连续）、30min的OHLCV聚合数学（open=首根开/close=末根收/high=max/low=min/vol=sum）、跨两个交易日不互相拼接。
- rangebreaks 3 例：只标真实缺失日、无缺口返空、空Series不崩。
- 指标 6 例：MA/BOLL/VWMA/MACD/RSI 在合成上涨趋势数据上的方向性/范围合理性，及未选任何指标返回空dict。
- 验证：`./venv/Scripts/python.exe -m pytest tests/test_chart_data.py` 12/12 通过；全量 `tests/` 跑 345 通过 + 4 个当时未修的既有失败（`test_dataflows_config.py`/`test_memory_log.py`）。这 4 个后来在 §17 的上游同步会话里查清根因并修复，详见该节。

### 已知限制（fail-open，不阻塞）
- 分钟周期K线仅 A股可用（非A股/无本地vipdoc/pytdx不通 → 提示"该周期暂无数据"，与 §13 日内结构工具同一降级哲学）。
- 本地分钟线历史仅到 2026-03（TDX软件运行后才积累，详见 §14 数据覆盖核查），分析较早日期时分钟周期图可能为空，日/周/月线不受影响。

---

## 17. 同步官方 upstream v0.3.0（2026-06-22 续，Opus 4.8）

> 背景：项目 fork 到 `https://github.com/TravisSFWang/TradingAgents`，发现官方当天发布 v0.3.0（39个提交）。流程：先建 GitHub 远程结构 → checkpoint 本地未提交工作 → 在隔离分支合并上游 → 逐个解决冲突 → 测试全绿 → 快进 main。

### 远程结构变更
- `origin` 改名为 `upstream`（指向 `TauricResearch/TradingAgents`，只用来拉官方更新，不再 push）。
- 新增 `origin` 指向 `https://github.com/TravisSFWang/TradingAgents.git`（用户自己的 Fork，未来 push/PR 都走这里）。
- 本仓库设了 repo-local git 身份（非 `--global`）：`user.name=TravisSFWang`、`user.email=traviswang97@icloud.com`。

### v0.3.0 关键内容（详见仓库根目录 `CHANGELOG.md`）
- **Provider 注册表重构**：`llm_clients/` 整个目录改成统一 provider spec 模式，**官方原生加入了 Kimi/Moonshot、NVIDIA NIM、Groq、Mistral、Amazon Bedrock**。Kimi 的 provider key 是 `"kimi"`，环境变量 `MOONSHOT_API_KEY`，base_url `https://api.moonshot.ai/v1`，模型目录暂时是"仅自定义"（`model_catalog.py` 里 `_CUSTOM_ONLY`，没有下拉项，得手填 model id）。**这意味着之前讨论的"自己接 Kimi"不用做了，填 key 直接能用**。
- **验证过的数据访问契约**："配置的 vendor 链就是唯一解析路径，不再悄悄回退到未选中的 vendor"（`interface.py::route_to_vendor` 改写）。`VendorError` 类型化、过期 OHLCV 拒绝、look-ahead-safe 新闻窗口。
- **结构化输出加固**："thinking 模型解析失败时退化为自由文本"——官方版本的退化兜底，跟 CLAUDE.md §15 记的 DeepSeek 退化 bug 是同一类问题的官方修复。
- 移除了 `analyst_concurrency_limit`（no-op 配置项，本项目未引用，无影响）；移除 `uv.lock`；新增 CI gate（GitHub Actions：pytest + ruff + 装包烟测，Python 3.10–3.13）。

### 冲突解决（4 个本地 patch 过的官方文件，全部跟上游本次改动重叠）
1. **`market_analyst.py` / `agent_utils.py`**：纯 import 列表冲突（官方新增 `get_instrument_context_from_state`/`__all__`/`prediction_markets_tools`，本地新增 `intraday_structure_tools`），合并即可，无逻辑分歧。
2. **`interface.py`（vendor 路由层，官方这次改动最大，148行）**：保留了 `ASHARE_VENDOR_BEGIN/END` 自动生成块（`install_ashare_patch.py` 注入、调 `ashare_vendor.register()`）。核对后确认：`register()` 里 `ASHARE_VENDOR_PRIORITY` 路径（往 `data_vendors[cat]` 配置字符串前面拼 `"ashare,"`）跟新版"严格按配置链路由"完全兼容——新路由本来就是把 `data_vendors` 字符串按逗号拆开做 vendor_chain，我们的拼接方式正好命中这个契约。顺手删了 `register.py` 里一行已经死掉的 `itf.VENDOR_LIST.insert(0, "ashare")`（新路由根本不读模块级 `VENDOR_LIST`，全仓库搜索确认它现在是孤儿变量）。
3. **`trading_graph.py`（真正的决策冲突，不是机械合并）**：官方修了 `_fetch_returns` 里 yfinance 的 symbol 映射 bug（#984，如 XAUUSD→GC=F），但方案仍是走 yfinance。**保留了本地版本**（结算改走 `ashare_vendor.market_data.get_close_series`，非A股代码不回退雅虎）——这是 CLAUDE.md §9 记录过的刻意产品决定，不是待修 bug，合并时不该被上游修复悄悄覆盖回去。留了行注释：以后恢复美股结算源（§10 末项）时，记得把 #984 的 normalize_symbol 映射一并接回来。顺手删了因此变成死代码的 `import yfinance as yf` 和 `normalize_symbol` import。

### 测试：4 个既有失败一并查清楚根因并修复（不是这次合并引入的新问题，但根因跟今天动的代码同源）
- **`test_dataflows_config.py` 两个**：根因是 `ashare_vendor.register()` 在 import 时**全局原地改了 `DEFAULT_CONFIG`**（往 `data_vendors[cat]` 拼 `"ashare,"`）——这是刻意设计、承重的机制（保证任何地方 `config = DEFAULT_CONFIG.copy()` 都自动带上 A股优先级），不能改成"只调 `set_config()`"（那样 `TradingAgentsGraph.__init__` 后续的 `set_config(self.config)` 会用纯净默认值覆盖回去，A股优先级在真实运行中会失效）。改法：把测试里硬编码的字面值 `"yfinance"` 换成运行时读到的 baseline 再比较——这两个测试本来测的是"深拷贝隔离"语义，不应该绑死具体 vendor 字符串，换法对任何未来的默认值定制都更稳健，不只是为了兼容我们。
- **`test_memory_log.py` 两个**：跟 `trading_graph.py` 那个决策冲突同根——这两个测试 mock 的是 `yfinance.Ticker`，但本地 `_fetch_returns` 对非A股代码根本不调 yfinance，mock 是死的。同组里另外两个测试碰巧没暴露问题，是因为它们断言的结果本来就是 `(None, None, None)`，跟"非A股直接返回 None"撞车蒙混过关。把失败的两个改成 mock `ashare_vendor.market_data.get_close_series`，ticker 换成真实A股代码（600519），让测试真正跑到结算逻辑上。
- 验证：`./venv/Scripts/python.exe -m pytest tests/` **529 passed, 1 skipped**（可选依赖 `langchain-aws`，装了才能用 Bedrock）。另做了端到端 smoke test：`TradingAgentsGraph` 实例化成功，`data_vendors` 实际值确认是 `ashare,yfinance`（4个类别）+ `fred`/`polymarket`（不受影响，符合预期）。

### 合并落地
- 流程：checkpoint commit on `main`（5843cf2）→ 开 `sync-upstream-v0.3.0` 分支合并上游 → 解决4个冲突 → 测试全绿 → 修4个既有失败 → `git checkout main && git merge sync-upstream-v0.3.0 --ff-only`（纯 fast-forward，无新冲突）→ 删除已合并的临时分支。
- `main` 现在领先 `upstream/main` 3 个提交（checkpoint + merge + test fixes），未 push 到任何远程。
