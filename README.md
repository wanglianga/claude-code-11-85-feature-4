# 医保风险线索关系分析平台（RiskGraph）

面向医保稽核部门的**跨机构风险线索关系分析**演示系统：从就诊、处方、结算、药店库存/供药、
医师排班、转诊、养老床位与申诉材料中构建**可回溯、受限扩展、可反证、可复演**的关系图，
帮助稽核人员发现值得人工核验的跨机构异常。

> ⚖️ **合规红线**：规则分与图模型分都只是“风险结构 + 核查优先级”，**不是欺诈或违法结论**。
> 家庭共用联系方式、养老机构代办、慢病协作不会被直接解释为欺诈；弱身份证据不自动合并个人。
> 所有案件定性必须经人工核查与法定程序。

技术栈：**Vue 3 + Vite + Element Plus（SVG 自研图谱）｜ FastAPI + SQLAlchemy ｜ PostgreSQL**。
一键部署：**Docker Compose（PostgreSQL 16 + FastAPI 同源托管前端 dist）**。

---

## 1. 功能与题目要求对照

| 题目要求 | 实现位置 |
| --- | --- |
| **服务活动时间重建**：来源画像存机构/清算批次/服务发生时间/签收时间/批次内序号；分机构延迟模型；按服务时点重建日级就诊/结算活动与覆盖区间；异常解释器分离真实服务峰与传输批次峰并输出来源质量反馈 | 表 `source_profile / clearing_batch / activity_record`；`services/activity.py`；页面 **活动时间重建**（路由 `/activity`）；接口 `/api/activity/*`；验收 `activity_*` 共 5 项 |
| 来源账：来源系统 / 业务有效时间 / 接收时间 / 撤销更正 / 可引用字段 | 表 `source_record`；页面 **来源账**；每条业务记录均挂 `source_id`，支持更正链 `supersedes` |
| 身份解析分级：伪标识、有效期内专业凭证=强；共享电话/地址/代办人=弱候选，不自动合并 | `services/identity.py`；页面 **身份解析**；弱证据仅 `pending`，可人工确认/驳回并产生簇新版本+快照 |
| 边保存具体依据、来源与支持/反驳方向 | 表 `graph_edge`：`basis / source_codes / stance(support/refute/neutral) / status(active/negated)` |
| 图快照：冻结截止日、身份簇版本、边范围、政策版本 | 表 `graph_snapshot`；页面 **快照复演**（冻结图谱、四要素、两快照 Diff） |
| 模式求值：多点重复购药 / 拆方 / 集中结算 / 异常转诊的必要事实 | `services/patterns.py`；页面 **模式与排序**、案件详情“必要事实” |
| 反证编排：进销存、配送签收、停诊、床位服务、政策、申诉结果 | `services/counterevidence.py`；页面 **反证联查** 与案件“反证”页签 |
| 案件投影：最小子图、金额范围、证据出处、人工状态；分数非结论 | `services/projection.py`；页面 **案件投影 / 案件详情**；按统筹区/机构做接口级权限 |
| 扩展范围=起始索赔/受益人+允许边；同伪标识/强簇、处方/结算/供药、两跳、180 天 | `services/expansion.py` 默认参数；页面 **受限图扩展** |
| 弱身份候选只生成待核边，不能继续扩展全网 | BFS 中弱边对端标记 `terminal_candidates`，不入队；验收 `p23_weak_no_expand` |
| 高金额跨机构扩展须模式配置增加边与截止条件并重新冻结快照 | `expansion.high_amount_extension` + `POST /api/graph/high-amount-extension`；C-001 被门槛拒绝、C-007 授权并可冻结 |
| 规则组合筛选风险结构 → 金额 + 反证完整度排序 | `services/ranking.py`，排序选项 `amount_completeness / amount_unresolved / rule` |
| 带时间切分验证的图模型：地区/机构偏差校准、高分可追溯路径与反证 | `services/graphmodel.py`（纯 Python 逻辑回归，train≤2026-01-31 / val=2026-02；验证集指标真实计算；地区+机构类型残差校准） |
| 规则与模型在 P1 上展示**相同支持链** | 模型结果直接回传规则引擎的 `support_chain` 为 `trace_chain_uids`；验收 `same_chain_and_rank` |
| 变更（身份裁定/政策有效日/退费更正）→ 新快照 + 标出受影响案件；旧材料保留原判断来源 | `routers/identity.py`、`routers/snapshots.py`；`case_judgment` 不可变版本，旧版本永久指向旧快照 |
| 虚构网络四类验收原型 | 见下节，**总览页“一键验收”** 或 `GET /api/acceptance/run`（**15 项断言**：原 10 项 + 活动时间重建 5 项） |

### 虚构验收网络（全部为虚构数据）

- **P1 / C-001（多点重复购药，成立支持链）**：伪标识 `P1`（含药店建档镜像 `P1B`，强簇）
  在 **康宁（朝阳店）PH1** 与 **百草堂（东城店）PH2** 同日（2026-03-10）取得同成分 **氨氯地平**，
  数量 30/28、给药期重叠 28 天，两笔结算互相落在同一给药期间；开方医师陈明 `D1` 当日排班 **停诊 SCH1**
  且无多点执业备案。规则与图模型给出完全相同的核心支持链，在“多点重复购药”同目标开放线索中均排第一。
- **P2/P3 / C-002（弱身份，不并人）**：共用养老机构电话 `010-88886666` 与代办社工（两条弱证据），
  但身份簇始终分离；各自处方、本人签收 `DL1/DL2`、养老床位 `BED2/BED3` 均匹配。弱边不可继续扩展。
- **P4 / C-003（合法供药）**：表面同日重复购药由药店间调拨 `TR4`（PH4→PH3，批号一致）、
  进销存出入库闭环与串户结算全额红冲更正 `SRC-ST5B-C2` 解释；重叠边置 `negated`，不再进入扩展。
- **P5 / C-004（反证关闭）**：申诉 `APP1` 成立，退货验收单 `TH-0318` 与 `return_in` 台账印证药品退回，
  结算全额退款，案件 `closed_returned`。
- 另有：**C-005** P9 同诊三张处方（拆方）、**C-006** PH5 同终端 60 分钟四人集中结算、
  **C-007** P14 90 日三次同向转诊 + ¥88,200 结算（高金额跨机构扩展）、
  **C-008** P6 慢病协作月度续方（给药期不重叠 + 长处方备案，不命中并排除）。

---

## 2. 一键 Docker 启动（推荐验收方式）

```bash
cd /workspace/85-feature-4
docker compose up -d --build
# 等待 app 健康（首次会自动建表并播种虚构网络）
docker compose ps
```

- 平台：<http://localhost:8000>
- Swagger / OpenAPI：<http://localhost:8000/docs>
- 数据库端口映射到宿主机 **5433**（账号/库/密码均为 `ccdev`），避免与本机 PostgreSQL 冲突。
- 停止：`docker compose down`；清空数据卷重来：`docker compose down -v`。

> 说明：本交付容器内未安装 docker，因此镜像构建未在开发容器中实际执行；
> Dockerfile 采用与已验证本地流程完全一致的 Node 22 构建 + Python 3.12 运行步骤。
> 下列“本地开发与验证”均为在本容器预装运行时上**实际执行并通过**的结果。

---

## 3. 本地开发与验证（已实际执行）

### 3.1 数据库（容器内预装 PostgreSQL 18）

```bash
# 已验证 ccdev 可连；创建独立库（只需一次）
PGPASSWORD=ccdev /opt/postgresql-18/bin/psql -h 127.0.0.1 -U ccdev -d ccdev \
  -c "CREATE DATABASE riskgraph;"
```

### 3.2 后端（Python 3.12 + venv）

```bash
cd /workspace/85-feature-4/backend
/opt/python-3.12/bin/python3 -m venv /workspace/85-feature-4/.venv
/workspace/85-feature-4/.venv/bin/pip install -i https://pypi.tuna.tsinghua.edu.cn/simple \
  -r requirements.txt

# 初始化/重置播种（也可跳过：API 启动时空库会自动播种）
DATABASE_URL='postgresql+psycopg2://ccdev:ccdev@127.0.0.1:5432/riskgraph' \
  /workspace/85-feature-4/.venv/bin/python -m app.seed

# 启动（本地开发端口与 vite proxy 对齐为 8000）
# STATIC_DIR 可不设：config.py 默认解析到仓库根 frontend/dist
DATABASE_URL='postgresql+psycopg2://ccdev:ccdev@127.0.0.1:5432/riskgraph' \
STATIC_DIR=/workspace/85-feature-4/frontend/dist \
/workspace/85-feature-4/.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
```

- 健康检查：<http://127.0.0.1:8000/api/health>
- Swagger：<http://127.0.0.1:8000/docs>

### 3.3 前端（Node 22）

```bash
cd /workspace/85-feature-4/frontend
/opt/node-22/bin/npm install --registry=https://registry.npmmirror.com
# 若容器内 ~/.npm 为 root 所有导致 EACCES，可加：--cache /tmp/npmcache

# 方式 A：开发服务器（/api 代理到 127.0.0.1:8000，可用 API_TARGET 覆盖）
/opt/node-22/bin/npm run dev        # http://localhost:5173

# 方式 B：构建后由 FastAPI 同源托管（上面的 STATIC_DIR 指向 dist）
/opt/node-22/bin/npm run build
```

### 3.4 测试账号

| 账号 | 密码 | 权限 |
| --- | --- | --- |
| `admin` | `admin123` | 管理员，全部统筹区/机构；可重置数据、重训模型、触发变更 |
| `auditor` | `audit123` | 朝阳片区稽核员（`region_scope=["CY"]`），仅可见 C-001/C-004/C-006，访问 C-002 详情返回 403，可验证投影权限收敛 |

---

## 4. 本题验证方式与已执行结论

### 4.1 一键验收（15 项断言，全部通过）

- 界面：**总览 / 验收剧本 → 运行验收**（或登录后 `GET /api/acceptance/run`）。
- 命令行实测：

```bash
TOKEN=$(curl -s -X POST http://127.0.0.1:8000/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"admin123"}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["token"])')
curl -s http://127.0.0.1:8000/api/acceptance/run -H "Authorization: Bearer $TOKEN"
```

开发过程中的真实执行结果（服务层与 HTTP 层均跑过；下列为本次新增功能后的实测）：

```
OVERALL True 15 / 15
PASS source_ledger            # 106 条来源、0 缺引用、SRC-ST5B 更正链
PASS identity_grading         # P1/P1B 同强簇；P2/P3 异簇，IE-03 pending
PASS p1_chain                 # 同日/重叠28/同一给药期/停诊 + 7 条核心边
PASS same_chain_and_rank      # trace_chain_uids == support_chain；规则/模型同目标均 C-001 第一
PASS p23_weak_no_expand       # P3 为 terminal，ST4/RX4 不可达
PASS p4_transfer              # TR4 checked_explains，overlap negated 且扩展不可见
PASS p5_appeal_return         # APP1 upheld/returned，C-004 closed_returned
PASS restricted_expansion     # 2 跳 / 180 天，SCH1/PH1/PH2 可达，无越窗边
PASS high_amount_gate         # C-001 被拒、C-007 授权扩展并可达 bed 节点
PASS snapshot_immutable       # 快照#1 四要素齐全，判断 v1@snap1 带免责声明
# —— 本次“服务活动时间重建”新增 5 项 ——
PASS activity_profiles            # A/B/C 画像 + 五要素齐全；就诊/结算两类均 >60 行
PASS activity_peak_separation     # B 3/16 接收87==WE2清单，当日真实服务14，衰减0.839，真峰3/13=32
PASS activity_two_methods         # 反卷积与批次对账同估31天；总量均=477；批次点区间更窄
PASS activity_c_no_batch_guard    # C no_batch_no；跨机构套用 B 日历被 guard 拒绝
PASS activity_unknown_preserved   # 7 笔服务时间缺失只入 unknown，对所有服务日贡献=0，不落到3/31
```

### 4.1.1 服务活动时间重建：样例与人工核验点（本次新增，已实际执行）

新增页面 **左侧导航「活动时间重建」**（`/#/activity`，任意账号可登录访问），或直接调 API。
样例目标期间固定为 **2026-03-01 ~ 2026-03-31**，三家虚构机构：

| 机构 | 报送机制 | 延迟模型（实测，就诊口径） | 批次对账 | 重建效果 |
| --- | --- | --- | --- | --- |
| **H-A** | 每个服务日当晚稳定直报 | 平均延迟 **0 天**、当日达 100%，点核 `p(0)=1` | 每日批次，完整 | 反卷积/对账均**精确**；另有 **7 笔**月末签收但**服务时间缺失** |
| **H-B** | **周四~周日四天压到周一**（另有周三批、月末 3/26–31 合并清算批） | 均迟 **2.01 天**、支撑 0–5 天，固定压批日历使“混合延迟核”被误设 | 8 个批次清单全部四方对平、序号连续 | **批次对账精确（477 笔，点区间）**；通用反卷积仅近似且自动标 `low` 可信、给宽区间 |
| **H-C** | **无批次号**，但服务发生时间可靠 | 平滑 0–2 天短尾（0.6/0.3/0.1 拆分），回测日 MAE≈1.1 | **不可用 `no_batch_no`** | 直接按服务时点精确；自身核反卷积高可信 |

已用 HTTP + 服务层真实跑过、可复算的关键数字（`kind=visit`）：

- **接收日峰值如何改变（全机构）**：原始签收口径最高峰 **2026-03-31 = 117 笔**（月末清算批 + A 未知服务时间调整批），
  该日真实服务仅 **42 笔**，峰值当日虚高衰减 **64.1%**；按服务时点重建后最高峰变为
  **2026-03-13 = 96 笔**（一次真实服务高峰，3/12=93）。
- **B 机构更典型**：**2026-03-16（周一）签收 87 笔**，恰等于批次 `QPB-WE2-03` 清单中 3/12–3/15 四天就诊量之和；
  而 3/16 当天真实服务只有 **14 笔**，衰减 **83.9%**；真实服务峰在 **3/13（32 笔）**，且被压到 3/16 签收。
  → 接收日的“周一高峰”是**传输批次峰**，3/12–13 的高峰才是**真实服务峰**，异常解释器分别成组输出。
- **两条方法估计同一目标期间并报告不确定范围（B，31 天）**：
  - 延迟反卷积：总量估计 **477**（质量守恒，=有服务时间真值），区间约 **[176, 796]**（核误设→宽，并标低可信）；
  - 清算批次对账：**477，区间 [477,477]**（清单完整、四方对平，高精度）。
  - 全机构反卷积总量 **1431 ∈ [769, 2118]**，区间上限另含 **7** 笔未知服务时间。
- **C 为何不能套用 B 的批次校正**：C 的 `activity_record.batch_no` 全为 `NULL`、无 `clearing_batch` 清单，
  没有与 B 批次清单连接的键；且“周末四天→周一”是 **B 机构特定的压批日历**，不是通用延迟规律，照搬会把
  B 的服务日结构虚构到 C 头上。接口对 `org=H-C` 返回 `batch.available=false / reason_code=no_batch_no`，
  服务层 `why_cannot_transfer_batch('H-B', C)` 显式返回 `allowed=false / target_has_no_batch_no`（验收 `activity_c_no_batch_guard`）。
- **缺失服务时间不分配到月末**：A 有 3 行 `ACT-A-UNK-1..3` 共 **7 笔**，均于 **2026-03-31 19:30 签收**但
  `service_date=NULL`。它们进入 `unknown.units=7 / by_signoff={'2026-03-31':7}`，对所有服务日序列贡献为 **0**；
  `direct` 合计 = `known_service_units=1431`，而 `all_units=1438`。系统**不会**把这 7 笔计入 3/31 服务量，
  只在总量不确定区间上界中体现“可能属于目标期”。

相关接口（均需 Bearer 登录）：

```bash
B=http://127.0.0.1:8000   # TOKEN 取上一步登录返回的 token
curl "$B/api/activity/profiles"                 # 三画像 + 延迟直方图 + 质量反馈 + 跨机构套用 guard
curl "$B/api/activity/reconstruct?org=ALL&kind=visit"      # 曲线/两法/峰值分离 主编排
curl "$B/api/activity/reconstruct?org=H-B&kind=visit"       # 单机构：B 批次精确 vs 反卷积宽区间
curl "$B/api/activity/reconstruct?org=H-C&kind=settlement"  # 结算口径同样可用
curl "$B/api/activity/batches?org=H-B"          # 清算批次清单与对账状态/manifest
curl "$B/api/activity/records?unknown_only=true"          # 五要素明细 / 未知服务时间
```

UI 核验路径：登录 → 左侧 **活动时间重建** → 默认“全部/就诊”即可看到红柱（签收）与蓝线（服务时点）错位、
顶部 KPI 的衰减与峰位移；机构切到 **H-B** 勾选“批次对账线”可见其与蓝线完全重合，勾选“反卷积+区间”可见宽紫带与
低可信标注；切到 **H-C** 中部批次卡片显示“不可用/无批次号”及禁止套用 B 的说明；底部“来源明细”打开
“只看服务时间缺失”可见 3 行红色“未知·不分配”。

### 4.2 已实际执行的其他验证

- `python -m app.seed` 成功：`15 persons, 106 source records, 115 nodes, 267 edges, snapshot #1`
  （新增活动重建的 3 条报送来源 SourceRecord 与 `source_profile=3 / clearing_batch=40 / activity_record=313`，
  其中明细 `visit=158 / settlement=155`）。
- **空库首启链路实测（与 Docker 完全一致）**：`DROP/CREATE DATABASE riskgraph` 成 0 表空库后启动 uvicorn，
  startup `create_all` 建新三表并 `run_seed(reset=False)` 自动播种成功；随后 HTTP 登录、
  `GET /api/acceptance/run` 实测 **15/15 全绿**，`GET /` 与新构建的 `assets/index-*.js` 均 200。
- **活动时间重建（本次新增）实际执行**：服务层逐机构复算 + HTTP（admin/auditor 登录、未登录 401）+
  验收 5 断言均通过；`npm run build` 成功产出 `ActivityTime-*.js (≈24.2 kB)`。本容器**没有无头浏览器**，
  因此未对新页面做真实点击/截图；已用生产构建（模板与 `<script setup>` 全部编译、导入解析通过）+
  接口返回结构核对 + 空数据守卫审查替代，建议按 4.1.1 末尾路径做一次人工目检。
- 遍历全部 GET API 与 **8 个案件详情**均 200，投影子图 5~11 节点 / 6~18 边，每案 6 条反证核查。
- 权限：`auditor` 仅列出 `['C-001','C-004','C-006']`，访问 `GET /api/cases/C-002` 返回 **403**。
- 变更流（实测）：驳回 IE-03 → 快照 #2/身份簇 v2/影响 C-002；
  政策换版 → 快照 #3/影响 C-008 并追加判断 v2；
  ST12 退费更正 → 快照 #4/影响 C-005 并追加判断 v2（v1 保留）；
  案件人工状态写入核查时间线。
- `npm run build` **构建成功**（Vue SFC 全部通过编译，产物由 FastAPI `/` 托管，实测 200）。
- 使用 Playwright + 无头 Chromium（CDP 方式）真实打开并逐页巡检了登录、总览验收、来源账、身份解析、
  受限图扩展、模式排序、反证联查、案件列表/详情、快照复演共 10 个界面：登录跳转、一键验收全绿、
  图谱力导向渲染/边配色/支持链均正常，**浏览器 console 0 错误**，并逐页截图做了人工目检
  （过程中发现并修复了“边坐标取错字段导致连线不显示”的真实渲染 bug）。
- 重置接口曾出现“请求会话持锁 + drop_all”自死锁，已通过重建前先 `rollback()/close()` 修复并复测秒回。
- 容器内**没有 docker**，因此未执行 `docker compose build`；Dockerfile/Compose 仅做了 YAML 解析与逻辑核对，
  镜像构建步骤与已验证的本地 Node/Python 流程一致。建议在装有 Docker 的机器上执行第 2 节命令做最终确认。

### 4.3 建议的人工 UI 走查（约 5 分钟）

1. `admin/admin123` 登录 → 总览 → **运行验收**，应全绿。
2. **身份解析**：看到 IE-01/IE-02 强证据自动成簇、IE-03~06 弱候选；可对 IE-03 驳回，提示新快照与 C-002。
3. **受限图扩展**：起点 `P1` 扩展，点红/橙边查看 `basis` 与来源编号；切到 `P2` 可见黄色虚线圈 P3 且无法延伸；
   高金额面板对 C-001 拒绝、C-007 授权并可冻结。
4. **模式与排序**：MULTI_POINT 下规则/模型两种排序 C-001 均第一；进 C-001 详情核对事实、
   完全相同的支持链 UID、模型特征贡献与校准因子。
5. **反证联查**：切换 C-003/C-004 看绿色“合理解释/确认反证”，C-001 看红色“仍未排除”。
6. **案件投影**：提交核查状态；切换判断版本；用 `auditor/audit123` 重新登录验证只能看朝阳案件。
7. **快照复演**：查看 #1 冻结图与政策/身份版本；执行政策换版或 ST12 退费，查看新快照、Diff 与受影响案件，
   再回 C-005/C-008 对比 v1 与新判断。
8. **活动时间重建（本题新增）**：左侧进入该页，默认全部/就诊即可看到红柱（签收）与蓝线（服务时点）错位、
   KPI 显示 raw peak `2026-03-31 117 → 当日服务 42（衰减 64%）`、真峰移到 `03-13 96`；机构切 **H-B**
   可见 `03-16 87→14（衰减 84%）`，勾选“批次对账线”与蓝线重合、勾“反卷积+区间”见宽紫带与低可信标注；
   切 **H-C** 见批次方法“不可用/无批次号”及禁止套用 B 的说明；底部“来源明细”打开“只看服务时间缺失”，
   可见 3 行共 7 笔红色“未知·不分配”。就诊/结算切换、清算批次 manifest 展开均可用。

---

## 5. 目录结构

```
/workspace/85-feature-4
├── Dockerfile                 # 多阶段：node22 构建 → python3.12 托管
├── docker-compose.yml        # db(postgres:16) + app，健康依赖
├── README.md
├── backend/
│   ├── requirements.txt
│   └── app/
│       ├── main.py            # FastAPI、启动播种、静态托管、挂载 activity 路由
│       ├── config.py database.py models.py security.py acceptance.py seed.py
│       ├── routers/           # auth/sources/activity/identity/graph/patterns/cases/snapshots/tools
│       └── services/          # activity(延迟核/R-L反卷积/批次对账/峰值分离/质量反馈)
│                              # identity/graph_builder/patterns/counterevidence/
│                              # graphmodel/expansion/projection/snapshot/ranking/rejudge
└── frontend/
    ├── vite.config.js package.json
    └── src/
        ├── App.vue AppLayout.vue main.js router.js api.js store.js styles.css
        ├── components/GraphCanvas.vue          # 自研 SVG 力导向图
        ├── components/ActivityCurveChart.vue   # 自研 SVG 接收柱+服务线+不确定带
        └── pages/             # Login/Dashboard/Sources/ActivityTime/Identity/Explorer/Patterns/
                               # Cases/CaseDetail/Counter/Snapshots
```

## 6. 主要 API 一览

```
POST /api/auth/login
GET  /api/meta/stats
GET  /api/acceptance/run
POST /api/meta/reset                         (admin)
GET  /api/sources?source_system=&record_type=&status=&q=
GET  /api/sources/{code}
GET  /api/activity/profiles                         # A/B/C 画像+延迟直方图+质量反馈+guard
GET  /api/activity/reconstruct?org=ALL|H-A|H-B|H-C&kind=visit|settlement
GET  /api/activity/batches?org=                     # 清算批次清单/manifest/对账状态
GET  /api/activity/records?org=&kind=&unknown_only= # 五要素明细
GET  /api/identity/evidence | /clusters
POST /api/identity/evidence/{code}/adjudicate
GET  /api/graph/expand?start_type=beneficiary|claim&start_code=P1&max_hops=2&window_days=180
POST /api/graph/high-amount-extension
GET  /api/patterns/configs | /ranking?pattern_code=&sort=
POST /api/patterns/evaluate
GET  /api/patterns/model/latest ; POST /api/patterns/model/train
GET  /api/cases ; GET /api/cases/{code} ; GET /api/cases/{code}/judgments/{v}
POST /api/cases/{code}/review
GET  /api/counter...（反证数据内含在 /api/cases/{code}）
GET  /api/snapshots ; /{no} ; /diff?from_no=&to_no=
POST /api/snapshots/policy-change | /refund-correction
```

## 7. 边界与后续可加强

- 图模型是**可审计教学实现**（纯 Python 逻辑回归 + 确定性演练样本），用于演示时间切分、校准与路径追溯，
  不是生产级反欺诈模型；新型协作模式召回较低是规则法的已知特性。
- 认证为库内不透明令牌的轻量实现，生产应对接统一身份/审计网关并补充 HTTPS、CSRF 与更细字段级权限。
- 数据全为虚构；未使用 Redis（题目环境虽预装），无搜索/JSON 模块依赖。
