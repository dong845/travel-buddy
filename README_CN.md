# travel-buddy：先想清楚「去哪儿」，再给你一份真能照着订的行程

<p align="center">
  <a href="README.md"><strong>English</strong></a>
</p>

<p align="center">
  <a href="LICENSE"><img alt="License: MIT" src="https://img.shields.io/badge/License-MIT-yellow.svg"></a>
  <img alt="Claude Code" src="https://img.shields.io/badge/Claude_Code-supported-5b5bd6">
  <img alt="Codex" src="https://img.shields.io/badge/Codex-supported-111827">
  <img alt="输出" src="https://img.shields.io/badge/%E8%BE%93%E5%87%BA-%E8%87%AA%E5%8C%85%E5%90%AB_HTML_%2B_JSON-0f766e">
  <img alt="依赖" src="https://img.shields.io/badge/%E4%BE%9D%E8%B5%96-%E4%BB%85%E6%A0%87%E5%87%86%E5%BA%93-2f6feb">
  <img alt="数据" src="https://img.shields.io/badge/%E6%95%B0%E6%8D%AE-100%25_%E6%9C%AC%E5%9C%B0-16a34a">
</p>

<p align="center">
  <a href="#安装"><img alt="用 npx skills 安装" src="https://img.shields.io/badge/npx_skills-add_dong845%2Ftravel--buddy-000000"></a>
  <a href="#安装"><img alt="作为 Claude Code 插件安装" src="https://img.shields.io/badge/Claude_Code-%E4%BD%9C%E4%B8%BA%E6%8F%92%E4%BB%B6%E5%AE%89%E8%A3%85-5b5bd6"></a>
</p>

> **一个不肯编价格、不肯在没确认末班车之前说「可以订了」、也不肯在证明目的地根本到得了之前就给你排逐日行程的旅行助手。**

大多数 AI 行程工具，面对「我有 7 天、1500 欧」会直接给你一份信心十足的逐日行程 —— 而那座城市你从没选过。travel-buddy 把这当成两件事：先决定**去哪儿**（生成候选、做硬过滤、并说明淘汰了什么、为什么），只有目的地真正定下来之后才开始建行程，然后交付一个**自包含的 HTML 页面**：真实路线、真实预订链接、以及每一行都带来源和查询时间的人均预算。

---

## 这是什么

travel-buddy 是给 [Claude Code](https://claude.ai/code) 和 [Codex](https://openai.com/codex) 用的 skill。你在终端里跟它说话，它用本机浏览器表单收集需求，实时查证易变事实，最后在你机器上存下两份配对产物：

| 产物 | 内容 |
| --- | --- |
| `plans/<日期>-<标题>.json` | 结构化的完整方案 —— 每个选项、价格口径、来源 URL 与假设 |
| `html/<日期>-<标题>.html` | 单文件自包含页面：分时行程、逐段地图、预订卡片、预算表、来源登记 |

除了查资料的请求本身，没有任何数据离开你的机器。不需要账号，不同步云端，生成的页面里没有第三方脚本。

**整个 skill 建立在五条原则上：**

1. **把稳定推理和易变事实分开。** 偏好、约束、取舍逻辑由模型给；票价、时刻表、营业时间、入境规则、天气必须来自带访问日期的实时来源 —— 绝不能凭记忆。
2. **硬约束只做闸门，偏好只做排序。** 过不了硬过滤的目的地，不能靠「有魅力」翻盘。如果一个都活不下来，那本身就是**结论** —— 报告约束冲突和最小让步项，而不是硬推一个赢家。
3. **入境负担 = 目的地国家 × 旅行者身份，不是地理。** 「国内还是出境」是个错问题：持成员国居留许可的第三国公民进申根区完全免签。表单问的是你能接受多大**签证成本**，其余由此推导。
4. **只浏览，不下单。** 所有外链都是你自己点开的浏览型链接。skill 不登录、不填支付信息、不加购物车，也绝不会因为网页上显示了就把某项标成「已预订」。
5. **一个会失败的检查，胜过一段客气的说明。** 规则由四道闸门强制执行，而不是指望模型记得 —— 两道证明产物格式规整，两道证明它是真的。

---

## 它和别的不一样在哪

**它检查真正会毁掉行程的那件事。** 一次真实的齐齐哈尔→深圳行程：当地机场通航表只有八个航点，深圳不在其中 —— 所以「只接受直飞」在任何行程存在之前就已经不成立。返程航班是从当天**末班接驳动车（21:35）倒推**选出来的，而不是顺着一个好看的起飞时间往下排。而漫步那天的博物馆，正好周一闭馆。这些都不是一份「听起来合理」的行程会抓到的东西。

**它会告诉你渠道本身要花多少钱。** 同样四个航班，国内站 ¥4,259，国际站 ¥7,020 —— ¥2,761 的差价直接决定预算够不够。travel-buddy 会记录每个预订渠道的可达状态（`available` / `limited` / `unknown`），而不是假设「搜索结果看得见」就等于「你能买成」。

**它按目的地市场选服务商，不按品牌习惯。** 中国大陆的路线用带真实坐标的 `uri.amap.com/navigation` 导航链接；如果主路线用了 Google Maps，校验器会直接**判页面不合格**。POI 页面永远不被接受为导航。

**它假定自己会出错，并让错误发出声音。** 结构校验只能证明页面「格式规整」，永远证明不了它「是真的」—— 一次真实运行通过了全部结构校验，同时交付了：只写到签证、漏掉 EVUS 登记的入境结论（中国护照持有人会因此在值机柜台被拒登机）、把同一架飞机当成两个航空公司比价、把免费导览排在它根本不开的那一天、把晚餐排进 17:00 就打烊的店、以及在旅客明确要求「避免长距离步行」的前提下，把实际最重的一天标成「最轻」。下面两道闸门分别回应这两件事；没跑核验就保存的计划，会在自己的首页上说明这一点。

**它不会悄悄交一份半成品。** Construction 任务必须在磁盘上同时存在通过校验的 JSON 和 HTML、并报出两个确切路径，才算完成。达不到就必须标成「中间态探索」，并点名那一个卡住的问题。

---

## 它怎么工作

### 四种工作模式

模式由「你已经知道什么」决定，不是单独问你的：

| 你手上有 | 模式 | 你会拿到 |
| --- | --- | --- |
| 没有目的地，或只有一个大洲 | **Discovery（发现）** | 3–5 个排序候选，含取舍说明与淘汰记录 |
| 有国家/区域但没定城市 | **Constrained discovery（受限发现）** | 先比较子区域与城市，再谈行程 |
| 目的地已经定了 | **Construction（建行程）** | 完整逐日方案 + 两份交付物 |
| 已有方案，来了新约束 | **Incremental replanning（增量重排）** | 只重算受影响部分，并给变更日志 |

Discovery 不会悄悄塌缩成 Construction。范围写了 `fixed` 却没点名任何地点，会被判为**阻塞**，而不是替你猜一个。

### 它按什么顺序问（按决策影响力降序）

在说出「某地很合适」之前，这七项必须收齐或显式假设：

1. **出发地** —— 城市、国家、可接受机场（绝不从城市推断机场：同一个都市圈里，有人优先廉价二级机场，有人只要直飞）
2. **时间窗** —— 有确切日期就用确切日期，否则月份 + 时长，外加灵活度与必须迁就的固定事项
3. **同行人** —— 人数、相关年龄、行动/健康需求，以及饮食或宗教限制
4. **预算** —— **人均**，含货币、目标价与硬上限、以及涵盖哪些类别
5. **目的地范围** —— `fixed` / `anchored` / `continent` / `open`
6. **出行目的** —— 它对「怎样才算好的一天」的影响，比大多数偏好字段都大
7. **体验方向** —— 自然 / 人文 / 平衡，再选 2–4 个具体子类并排出前两名

所有只有在定了目的地之后才有意义的细节（房间数、早餐、退改、舱位、行李、地图 App）都折叠在可选区块里，好让真正决定成败的问题保持可读。

### 它凭现有信息肯走到哪一步

| 它知道 | 它会给 |
| --- | --- |
| 出发地、大致时间窗、粗预算、范围、大方向 | **探索性灵感清单**，并明确标注为探索性 |
| ……再加同行人与高影响过滤项（入境、旅行时长、天气、行动能力） | **排序推荐** |
| ……再加确切日期、入境状态、预算口径、住宿与行动能力全部确认 | **可预订方案** —— 且易变事实会在你下单前重新核实 |

只有过了硬门的候选才进入打分。推荐的起始权重：体验契合 25、全包性价比 20、季节契合 15、出发可达性与本地物流 15、舒适/人群/美食/语言/安全 15、灵活性与证据置信度 10 —— 并且只在你真正表达过的偏好之间分配。分数只是摘要，永远不是解释本身。

预订状态被严格区分：`idea`（纯灵感，未核实）→ `researched`（查到当前信息，未预留）→ `held`（你确认已锁位）→ `booked`（你确认已成交）。**绝不会因为网页上显示了就变成 `booked`。**

### 流水线

```
start_intake_workflow.py
    ├── 没有有效档案？ → 个人档案表单（一次性、需勾选同意、仅本机）
    └── 恰好一个？     → 直接复用、预填，并明确告诉你加载了什么
                    ↓
            本次行程表单（日期/同行/预算/范围 —— 每次都问）
                    ↓
            落盘：plans/intake-*.json  +  plans/next-action-*.json
                    ↓
            run_destination_discovery.py → 拉起一个全新的非交互 Codex/Claude 任务
                    ↓
        Discovery → 候选短名单        Construction → new_plan_skeleton.py → plan JSON
                                              ↓
                          五域并行核验              （references/verification.md）
                                              ↓
                                check_plan_consistency.py   （方案与自身是否自洽）
                                              ↓
                                render_final_trip_html.py   （校验方案结构）
                                              ↓
                                validate_trip_html.py       （校验页面）
                                              ↓
                                save_trip_deliverables.py   （全部跑一遍，然后落盘）
                                              ↓
                                check_link_targets.py       （需联网，由你手动跑）
```

### 四道闸门

四道全部在 `save_trip_deliverables.py` 内部自动执行。前两道证明产物**格式规整**，后两道证明它**是真的** —— 这是结构检查够不到的另一个维度。

四道都在 `save_trip_deliverables.py` 里自动执行。值得了解，因为它们查的是**不同的东西**：前两道证明产物「格式规整」，后两道证明它「是真的」—— 后者是结构校验永远够不到的另一个维度。

**`render_final_trip_html.py`** 拒绝渲染结构不全的方案：`route.segments` 数量必须精确等于 `len(stops_in_order) - 1`、每段只能有**一个主要方式**（「地铁/公交/打车」这种选择清单判为含糊）、每个整天都要有午餐**和**晚餐，以及十来条其他规则。

**`validate_trip_html.py`** 检查渲染后的页面。它最锋利的一条规则是：**只要页面 `<html lang>` 不是英文，页面里残留任何渲染器自带的英文就判失败。** 这正是 `plan_status`、预算类别、餐次、交通方式都被定义成封闭枚举的原因 —— 任意类别字符串无法翻译，就会以英文泄漏到中文页面上。它同时强制：地图按钮必须是真正的导航 URL、大陆路线必须用高德、预订与来源行必须带上可机器校验的属性。2.0 起还会拒绝任何「按钮上写的提供方 ≠ 链接实际打开的域名」的页面：曾有九个按钮写着 *在 KLM 查看选项* 却打开 Google Flights、写着 *在 Google Maps 查看餐厅* 却打开美食博客，而当时所有闸门全绿 —— HTTPS 与唯一性都说明不了链接会去哪里。

**`check_plan_consistency.py`** 把「散文靠不住」的部分交给代码判定：路线合计必须由自身 segment 求和、步行数字必须从数据推导而非手写断言、每顿饭必须挂在当天路线的真实站点上且落在营业时间内、日历不得有缺口、预算合计必须等于它声称求和的那些行且声称包含的每个类别都真的有明细。

2.0 的一次自审又找出它自己的三个盲区：行程日期反转（会让日期覆盖循环一次都不迭代，从而**静默关掉下游全部日期检查**）、负数路段（−25 分钟的一段能抵消真实的一段，算术却仍自洽）、以及某天声称的换乘次数少于它自己各路段声明之和。

**核验阶段**（[`references/verification.md`](references/verification.md)）负责只有真实世界能回答的部分：入境规则、票价与时刻、营业时间、该日期是否已开售、季节性事实。它拆成五个域，因为让**一次**调用同时查完五件事，每件只能分到五分之一的注意力 —— 那正是晚餐被排进已打烊餐厅的成因。运行时支持并发就并发；不支持就跑五趟**各自独立**的串行，而不是塞进一个 prompt。收益是注意力集中，不是墙钟时间。

### 另外两个脚本

**`new_plan_skeleton.py`** 生成一份结构合法、等着填内容的方案骨架。模板列得出每个字段，却表达不了字段之间的规则，这些以前只能靠撞失败去学 —— 一次实测为此损失了三轮返工和 21 个结构错误。所有待填值都是 `TODO:` 标记，`validate_trip_html.py` 拒绝含 TODO 的页面，所以「起步更快」不会变成「能交空壳」。

**`check_link_targets.py`** 逐个跟踪外链按钮，报告它实际落在哪里。**故意没有**接进 `save_trip_deliverables.py`：它需要联网，而一道会在飞机上或 CI 里失败的闸门最终只会被绕过。`broken` 判定刻意收得很窄 —— 只有硬 4xx/5xx 和跳转到不同域名。其余归为 `unverified`，因为服务商的回答取决于是谁在问：同一个 Google Flights 地址对浏览器返回 200 不跳转，对脚本却跳到 `unsupported` —— 这个检查的早期版本正因此错判过一条完好的链接。

`save_trip_deliverables.py` 没有核验报告就拒绝保存。`--unverified` 逃生口保留 —— 一道被绕开的闸门谁也警告不到 —— 但它的代价从「沉默」变成「可见」：落盘计划记录 `verification_status: unverified`，页面最上方渲染一条随语言本地化的**「未经事实核验」**横幅。

```bash
python scripts/new_plan_skeleton.py --start 2026-09-11 --end 2026-09-14 \
  --origin 阿姆斯特丹 --destination 马拉加 --language zh --currency EUR \
  --travellers 1 --mode public-transit --stops-per-day 4 > plan.json

python scripts/check_plan_consistency.py plan.json \
  --verification verification-report.json

python scripts/validate_trip_html.py final.html \
  --expected-days 4 \
  --require-booking-type flight --require-booking-type hotel \
  --transport-mode public-transit

python scripts/check_link_targets.py final.html
```

---

## 快速开始

### 安装

需要 **Python 3.10+**（开发环境为 3.13）。**不需要 pip 装任何东西** —— 所有脚本只用标准库。三条路任选其一。

**方式一 —— 用 [`npx skills`](https://github.com/vercel-labs/skills) 一行装完**（最省事）：

```bash
npx skills add dong845/travel-buddy
```

它会询问 agent 与安装范围。加 `-g` 全局安装（对所有项目生效），加 `-a claude-code`（或 `-a codex`）跳过 agent 选择，加 `-y` 全程非交互，加 `-l` 只列出发现的 skill 而不安装。仓库根目录本身**就是**这个 skill，所以整个目录会被复制进你的 skills 文件夹。

**方式二 —— 作为 Claude Code 插件安装**（可管理更新，也是唯一能覆盖云端会话的方式）：

```text
/plugin marketplace add dong845/travel-buddy
/plugin install travel-buddy@travel-buddy
/reload-plugins
```

插件里的 skill 带命名空间，所以调用形式是 `/travel-buddy:travel-buddy`。两点要注意：如果你**同时**在 `~/.claude/skills/` 里还留着手动装的副本，这个 skill 会出现两次 —— 系统不做去重，请把手动那份删掉。另外第三方 marketplace **默认不自动更新**，要拿新版本得跑 `/plugin marketplace update travel-buddy`。

**方式三 —— 克隆 + 软链**（打算改代码就选这个：改完立即生效，插件缓存做不到这一点）：

```bash
git clone --depth 1 https://github.com/dong845/travel-buddy.git ~/code_project/travel-buddy
ln -s ~/code_project/travel-buddy ~/.claude/skills/travel-buddy
```

不需要放在别处的话，也可以直接克隆进 skills 目录：

```bash
git clone --depth 1 https://github.com/dong845/travel-buddy.git ~/.claude/skills/travel-buddy
```

然后初始化一次工作区：

```bash
cd ~/.claude/skills/travel-buddy
python scripts/travel_workspace.py init          # 创建 ~/Travel Buddy/{profiles,plans,html}
```

### 开始用

在 Claude Code 里敲 `/travel-buddy`，或者直接描述需求 —— 说一句「帮我找个三月份暖和的地方待一周」就够触发了。

第一次用，让它走引导表单：

```bash
python scripts/start_intake_workflow.py --assistant auto
```

它会打印一个 `http://127.0.0.1:<随机端口>/` 链接。打开、填写、保存 —— 同一个标签页会自动跳到本次行程表单，提交后自动拉起一个新的 CLI 任务开始做短名单。**全程不需要下载、搬运、上传或粘贴 JSON，也不用打「继续」。**

```bash
# 先复核/修改已保存的稳定偏好，再进入本次行程表单
python scripts/start_intake_workflow.py --edit-profile

# 有多个档案时，传 ID（不是路径）
python scripts/start_intake_workflow.py --profile alice --assistant claude

# 完全关掉自动接续
python scripts/start_intake_workflow.py --assistant none
```

如果你不想用浏览器表单，直接说 —— 它会退回到一份紧凑的聊天式提问，并且只在当前任务里保留信息。

---

## 工作区与隐私

```
~/Travel Buddy/
├── profiles/   # 自愿保存的可复用旅行者档案
├── plans/      # intake、工作流事件、方案 JSON、发现任务日志
└── html/       # 最终的只读浏览型行程页
```

**会存的：** 国籍、居留国家与居留**身份类别**、语言、常住城市与可接受机场、常用货币、节奏、住宿档次、无障碍与饮食需求、去过的地方、心愿单、明确排除项。

**永远不存的：** 护照或证件号码与图像、签证有效期、支付或银行信息、账号密码、精确住址、本地身份证号、私人账号上下文。表单服务端还会**直接拒绝**包含这类字段的提交，而不是默默存下来。

只有你在表单里勾选同意，档案才会被创建。你最新一次的指令永远压过已保存的值。

**删除档案是刻意手动的** —— 没有 `forget` 子命令。先确认解析出的确切路径，再删那一个文件：

```bash
rm "~/Travel Buddy/profiles/<你点名的那个>.json"
```

绝不能为了删一个档案而清掉整个工作区。

---

## 遇到问题

**提交时报「不支持的本次旅行需求格式」。** 表单的工作模式必须和目的地状态一致（`fixed` → `construction`，`anchored` → `constrained_discovery`，其余 → `discovery`）。服务端是**故意**拒绝矛盾组合的，免得存下来的文件一边说目的地已定、一边说还需要帮你找目的地。

**自动接续没反应。** 看 `plans/destination-discovery-*.log`。如果 CLI 不在 `PATH` 里，runner 会明说。用 `--assistant none` 可以关掉自动接续、改为手动继续。

**`--edit-profile` 好像没生效。** 它只在已经存在档案时起作用；`profiles/` 为空时，流程会直接去创建新档案。

**刚建的档案校验通过，但里面是空的。** `create-profile` 写的是一个已同意的**空壳**，`validate-profile` 在所有实质字段仍为 null 时也会判 VALID。用 `--edit-profile` 填完再依赖它。

**校验器因为英文文本判我的页面不合格。** 非英文页面上，所有渲染器自带的字符串都必须翻译，**以可见文本形式打印出来的机器枚举值也算**。用封闭枚举，别自造类别名。

**地图按钮过不了校验。** 它必须是真正的导航 URL。中国大陆意味着 `https://uri.amap.com/navigation` 且 `from`、`to`、`mode` 都非空；`ditu.amap.com/place/...` 是 POI 页面，会被拒。另外记住高德用的是 GCJ-02 坐标系，WGS-84 坐标必须先转换，否则每条路线都会偏几百米。

---

## 安全

表单由临时 HTTP 服务提供，**只绑定 `127.0.0.1`**、使用随机端口、只接受一次有效提交然后自行关闭。页面里没有第三方脚本、没有远程请求、没有登录、没有支付、没有上传。

也请了解这个模型的边界：**回环绑定是唯一的屏障** —— 没有 Origin 校验、没有 CSRF token、没有认证，所以在它开着的那几秒里，本机任何进程只要猜到端口就能 POST 进来。缓解手段是随机端口、单次提交即关闭，以及对落盘内容的敏感字段扫描。

这个 skill 不会为了让被拦截的服务能用而建议 VPN、代理、账号变通或共享凭据，也不会代你完成预订、支付或账户变更。

---

## 开源协议

MIT，见 [LICENSE](LICENSE)。
