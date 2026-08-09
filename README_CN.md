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
  <a href="https://clawhub.ai/dong845/skills/travel-buddy"><img alt="在 ClawHub 上" src="https://img.shields.io/badge/ClawHub-%40dong845%2Ftravel--buddy-7c3aed"></a>
  <a href="https://skillhub.cn/skills/user_f486c577/travel-buddy"><img alt="在 SkillHub 上" src="https://img.shields.io/badge/SkillHub-travel--buddy-ff6a00"></a>
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
| 已有方案，来了新约束 | **Incremental replanning（增量重排）** | 只重算受影响部分，并给变更日志。改日期时走 `replan_trip.py`：它重写位移能决定的部分，并拒绝让其余部分继续冒充「已核实」 |

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


四道都在 `save_trip_deliverables.py` 里自动执行。值得了解，因为它们查的是**不同的东西**：前两道证明产物「格式规整」，后两道证明它「是真的」—— 后者是结构校验永远够不到的另一个维度。

**`render_final_trip_html.py`** 拒绝渲染结构不全的方案：`route.segments` 数量必须精确等于 `len(stops_in_order) - 1`、每段只能有**一个主要方式**（「地铁/公交/打车」这种选择清单判为含糊）、每个整天都要有午餐**和**晚餐，以及十来条其他规则。

**`validate_trip_html.py`** 检查渲染后的页面。它最锋利的一条规则是：**只要页面 `<html lang>` 不是英文，页面里残留任何渲染器自带的英文就判失败。** 这正是 `plan_status`、预算类别、餐次、交通方式都被定义成封闭枚举的原因 —— 任意类别字符串无法翻译，就会以英文泄漏到中文页面上。它同时强制：地图按钮必须是真正的导航 URL、大陆路线必须用高德、预订与来源行必须带上可机器校验的属性。2.0 起还会拒绝任何「按钮上写的提供方 ≠ 链接实际打开的域名」的页面：曾有九个按钮写着 *在 KLM 查看选项* 却打开 Google Flights、写着 *在 Google Maps 查看餐厅* 却打开美食博客，而当时所有闸门全绿 —— HTTPS 与唯一性都说明不了链接会去哪里。 2.1 起还会拒绝任何一张不打印评分的餐饮卡：评分只存在 JSON 里、从不渲染到页面上，与压根没去查是同一种缺陷——促成这条规则的那个页面，分数只出现在作者恰好手写进理由文字的地方。

**`check_plan_consistency.py`** 把「散文靠不住」的部分交给代码判定：路线合计必须由自身 segment 求和、步行数字必须从数据推导而非手写断言、每顿饭必须挂在当天路线的真实站点上且落在营业时间内、日历不得有缺口、预算合计必须等于它声称求和的那些行且声称包含的每个类别都真的有明细。

2.0 的一次自审又找出它自己的三个盲区：行程日期反转（会让日期覆盖循环一次都不迭代，从而**静默关掉下游全部日期检查**）、负数路段（−25 分钟的一段能抵消真实的一段，算术却仍自洽）、以及某天声称的换乘次数少于它自己各路段声明之和。

**2.1 补上的是一位旅客用鼠标点出来的三个缺陷——而在他点开之前，上面每一道闸门都是绿的。** 一份已交付的方案把自己的中文显示标签直接写进了地图 URL：`origin=酒店（拉斯坎特拉斯海滨）`——「酒店」这个普通名词加一句描述——被 Google 解析到了**台湾**，给出一条 65 小时的驾车路线。那份方案 15 个端点里有 6 个根本无法地理编码，而 `check_link_targets.py` 报告 25 条地图链接**全部 ok**，因为主机名对、状态码是 200。它的餐饮卡完全没有质量信号，于是一家在所有平台都查无此店的餐厅进了晚餐，两家 20:00 才开门的餐厅进了午餐。它的两家酒店共用一条字节完全相同的 Booking.com **城市**搜索链接，没有任何按钮能打开具体那一家——也就没人发现其中一家 7 晚 €1,256、光住宿就超过全部预算上限，而另一家在那些日期上根本没有空房。

- **`check_map_endpoints`** —— 地图 URL 的参数是地理编码查询，不是标题。端点必须是坐标；自由文本一律拒绝，因为地名有时能解析、有时会落到另一个大陆，而离线检查分辨不了这两者。`trip.destination_coords` 只需声明一次，却让每个端点从**相对**校验变成**绝对**校验：单靠「两端距离不超过该段声明距离」是相对的，把拉斯帕尔马斯某段两端都写成 `lon,lat`，两点仍相距 4.73 公里（正确值 4.70），而所有针位已移到非洲南部。它还会拒绝带途经点的公交 URL（Google 对这种请求根本不返回路线）、超过 15 公里的「步行」按钮，以及声称 `multi_stop` 却跳过当天中间站点的链接。
- **`check_venue_quality`** —— 每张餐饮卡都必须带评分及其分制、评价数与来源，或者明说「没有评分」并给出理由。卡片不得一边声称营业时间未核实、一边排出具体用餐时段：把一顿饭放上时钟，**本身就是**在断言那家店当时开门。它的门店链接必须以店名或 POI id 为键，而不是一句描述。
- **`check_booking_identity`** —— 比价链接必须限定到具体商品，让按钮打开那一家而不是一份列表。店名比对采用双向折叠后取子串，因为只认拉丁字符的切词器对「东京银座三井花园酒店」返回空，等于静默豁免了全部中日文市场。两个「候选」若打开同一个页面，那就是同一个选项展示了两次——这个缺陷在机票上和酒店上都发生过。

这三条随后不是被欣赏、而是被攻击了一遍；结果第一条的初版**并没有拦住它被写出来要拦的那个缺陷**：把已交付方案换回原来的中文标签、只改一行让任何一天都不再声称 `multi_stop`，检查器就退出 0。`tests/test_plan_consistency.py` 的第 23–27 节把全部 13 个攻击向量逐一钉死。

**2.2 继续攻击，而这次找到的东西大多在门禁自己身上。** 坐标解码器按数值范围去猜 `lat,lon` 还是 `lon,lat`，结果恰好搞坏了本技能强制使用非 Google 提供方的那个市场：北京因经度大于 90 而侥幸正常，乌鲁木齐按高德文档顺序正确书写却被读成纬度 87.6、判定为在 4,946 公里外的北冰洋——而作者若照错误信息去「检查坐标顺序」，会得到一个全绿的门禁和一堆指向北冰洋的按钮。两处评分下限都印着一个**没有任何代码会读**的出口（「或在 why_this_stop 里说明理由」），于是老老实实写理由的人被拒，而把 `rating_status` 翻成 `"none"`、低分原样留着的人直接通过。`_fold` 被写成白名单**两次**：第一版只认拉丁字母，第二版补上中日韩，却静默豁免了西里尔、希腊、泰、阿拉伯、希伯来与天城文。另外 2,500 公里的锚点半径会拒绝「纽约+洛杉矶」，3 倍绕行比会拒绝科罗拉多大峡谷南北缘——直线 18 公里、驾车 350 公里。

还有两条来得最有价值：有人打开页面看见了。整段话被印成 `这 · 是 · 路 · 线 · 概 · 览`——每个字都被点号隔开——因为 `transport_overview.notes` 是字符串**列表**，被写成了一个字符串，而渲染器要 join 它：遍历一个 `str` 得到的是单个字符。所有门禁都放行了，因为那个值是完全合法的字符串、那行 join 是完全合法的代码，**没有任何一处检查类型**。而由此新增的检查一开到已交付的计划上，又炸出 54 处一直在原样打印星号的 `**加粗**`，外加一句在路段已改为 35 分钟之后仍写着「约 25 分钟」的话。

本版新增四项检查——`check_implied_speed`、`check_list_typed_fields`、`check_prose_rendering`、`check_prose_agrees_with_data`——使 `PLAN_CHECKS` 从 14 项增至 18 项；测试套件第 28–37 节把上述每一条都从**两个方向**钉死，因为通过这类规则最省事的办法，就是让它永远不响。

**核验阶段**（[`references/verification.md`](references/verification.md)）负责只有真实世界能回答的部分：入境规则、票价与时刻、营业时间、该日期是否已开售、季节性事实。它拆成五个真实性域，外加两个完全不联网的审计员；因为让**一次**调用同时查完这么多事，每件只能分到很小一部分注意力 —— 那正是晚餐被排进已打烊餐厅的成因。运行时支持并发就并发；不支持就跑五趟**各自独立**的串行，而不是塞进一个 prompt。收益是注意力集中，不是墙钟时间。

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

需要 **Python 3.10+**（开发环境为 3.13）。**不需要 pip 装任何东西** —— 所有脚本只用标准库。四条路任选其一。

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

**方式四 —— 从 [ClawHub](https://clawhub.ai/dong845/skills/travel-buddy) 安装**（[OpenClaw](https://clawhub.ai) 智能体的 skill 市场）：

```bash
openclaw skills install @dong845/travel-buddy
```

travel-buddy 同时上架了 **[SkillHub](https://skillhub.cn/skills/user_f486c577/travel-buddy)**（面向中文用户的 skills 社区），适合在那里浏览和横向比较；安装仍然走上面四条路之一。

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

它会打印一个 `http://127.0.0.1:<随机端口>/?token=…` 链接。打开、填写、保存 —— 同一个标签页会自动跳到本次行程表单。提交之后会发生什么取决于你在哪里运行的命令，这个区别是**有意为之**：

- **在裸终端里**：自动拉起一个新的 CLI 任务开始做短名单。
- **在 Claude Code 或 Codex 内部**：`--assistant auto` 会**主动让位**，打印 `TRAVEL BUDDY TRIP INPUT: <路径>`，交给你正在对话的那个助手继续。它以前会在这种情况下另起一个无人看管的 agent —— 那个表示「已经有助手在处理这个工作区」的环境变量，反被当成了「再起一个」的信号 —— 结果是同一个文件夹里出现两份互相矛盾的方案。确实想要后台运行时，用 `--assistant codex` 或 `--assistant claude` 强制。

两种情况下都**不需要下载、搬运、上传或粘贴 JSON，也不用打「继续」。**

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

**自动接续没反应。** 在 `--assistant auto` 下这通常是**正确行为**而不是故障：当本 skill 跑在 Claude Code 或 Codex **内部**时，runner 会主动让位，把保存好的意向文件路径打印出来，交给你正在对话的那个助手继续。它以前会在这种情况下另起一个无人看管的 agent，结果是同一个工作区里出现两份互相矛盾的方案。从裸终端运行时它仍会启动；日志在 `plans/destination-discovery-*.log`，PID 和停止命令在 `plans/destination-discovery-*.pid.json`。如果 CLI 不在 `PATH` 里，runner 会明说。想强制后台运行用 `--assistant codex` 或 `--assistant claude`。

**`--edit-profile` 好像没生效。** 它只在已经存在档案时起作用；`profiles/` 为空时，流程会直接去创建新档案。

**刚建的档案校验通过，但里面是空的。** `create-profile` 写的是一个已同意的**空壳**，`validate-profile` 在所有实质字段仍为 null 时也会判 VALID。用 `--edit-profile` 填完再依赖它。

**校验器因为英文文本判我的页面不合格。** 非英文页面上，所有渲染器自带的字符串都必须翻译，**以可见文本形式打印出来的机器枚举值也算**。用封闭枚举，别自造类别名。

**它拒绝保存，报「没有核验报告」。** 这是闸门在正常工作。按 [`references/verification.md`](references/verification.md) 跑完那一轮核验 —— 五个真实性域加两个不联网的审计员，一共七个块 —— 保存报告，再用 `--verification <report.json>` 传进去。如果你是有意保存草稿，用 `--unverified`：它会照常落盘，并在页面顶部盖一条「未经事实核验」横幅，免得有人把它当成可预订版本。

**一致性闸门拒绝了一个看起来没问题的方案。** 读它点名的内容 —— 那是算术，不是口味。`walking_burden` 必须以**数字**引用由当天各 segment 算出的步行总量，这样文案就无法和数据脱节。每张餐卡都需要一个 `route_anchor` 指向当天的某个停靠点（或者用 `off_route_justification` 说明这段绕路的代价），并且要么有 `venue_hours`、要么写 `hours_status: "unverified"`。路线总计必须等于其各段之和。已声明的 `cap_per_person` 不能在没有 `overrun_acknowledged` 的情况下被突破。

**地图按钮过不了校验。** 它必须是真正的导航 URL。中国大陆意味着 `https://uri.amap.com/navigation` 且 `from`、`to`、`mode` 都非空；`ditu.amap.com/place/...` 是 POI 页面，会被拒。另外记住高德用的是 GCJ-02 坐标系，WGS-84 坐标必须先转换，否则每条路线都会偏几百米。

---

## 安全

表单由临时 HTTP 服务提供，**只绑定 `127.0.0.1`**、使用随机端口、只接受一次有效提交然后自行关闭。页面里没有第三方脚本、没有远程请求、没有登录、没有支付、没有上传。

回环绑定**不是**唯一的屏障。服务启动时会生成一次性 token 并写进终端打印的链接里，任何不带 token 的页面访问和提交都会被拒；跨站 POST 还会再被 `Origin` 校验拦一次，并且要求 `Content-Type: application/json`——这会触发预检，而本服务从不应答预检。此外有一把锁保证只接受一次提交，所以连点两下不会存两份、也不会拉起两个 agent。随机端口和对落盘内容的敏感字段扫描仍然在。

如实说明残余边界：**以你的身份**在**你的机器上**运行的任何进程，都能从终端或进程列表里读到那个 token。所以它防的是恶意网页，不是已经以你账号身份运行的本机恶意程序。

这个 skill 不会为了让被拦截的服务能用而建议 VPN、代理、账号变通或共享凭据，也不会代你完成预订、支付或账户变更。

---

## 开源协议

MIT，见 [LICENSE](LICENSE)。
