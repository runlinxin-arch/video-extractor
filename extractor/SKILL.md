---
name: video-extractor
description: 把一个 YouTube 链接变成一份可追溯的结构化中文笔记，落进 Obsidian 的「视频笔记/」。当用户说"把这个视频/播客做成笔记""帮我凝练这个 YouTube""把这条链接吃成笔记""这视频讲了啥，给我能点回去的笔记"，或贴出一个 YouTube 链接并希望得到摘要、时间轴、论点、术语表时使用。不用于：判断值不值得看、对话式问答、全文翻译、B 站/本地文件（v0.1 不支持）。
---

# video-extractor · 视频凝练器

把 **一个 YouTube 单条视频链接** → **一份可追溯的中文笔记**，写进 Obsidian。

产物：`视频笔记/YYYY-MM-DD 标题.md` + `视频笔记/底稿/YYYY-MM-DD 标题.json`

## 你的分工

| 谁 | 做什么 |
|---|---|
| **脚本**（本 skill 的 `scripts/extract.py`） | 取数、清洗、断句、校验 id、统计时间、组装、落盘。**它算时间码，你不算** |
| **你（智能体）** | 只做三件语义判断：**勘定**（切几段）、**提炼**（每段的主张）、**复核**（结论与原文是否对得上） |

**核心纪律：你永远不写时间码，只写 utterance id。** 时间由脚本查表得到。这是整条管线唯一防止"编造锚点"的机制。

## 0. 环境（首次）

```bash
cd <本 skill 目录>
bash setup_env.sh          # 建 .venv 并装 pyyaml；可选装 mlx-whisper
./.venv/bin/python scripts/extract.py preflight
```

`preflight` 失败 = 代理没开。让用户先开代理（默认 `127.0.0.1:7897`），不要重试硬等。

## 1. 一条链接的完整流程

```bash
QZ="./.venv/bin/python scripts/extract.py"
URL="https://www.youtube.com/watch?v=XXXXXXXXXXX"

$QZ preflight                                  # 1. 探活（失败即停）
$QZ fetch "$URL"                               # 2. 元数据 + 字幕/音频 → job.json
$QZ clean --sid <sid>                          # 3. 清洗 → utterances.json + quality.json
$QZ jieuding-pack --sid <sid>                  # 4. 生成勘定输入包
#    → 你读 jieuding_pack.txt，写 /tmp/jieuding.json，然后：
$QZ jieuding-check /tmp/jieuding.json --sid <sid>   # 5. 校验并解析出 units.json
$QZ unit-pack N --sid <sid>                    # 6. 逐段取提炼输入（N=1..段数）
#    → 你读 unit-N-pack.txt，写 <jobdir>/claims-N.json，全部段做完后：
$QZ tiliang-check --sid <sid>                  # 7. id 校验 → claims.json
$QZ review-pack --sid <sid>                    # 8. 生成语义复核包
#    → 你读 review_pack.json，写 /tmp/review.json，然后：
$QZ review-apply /tmp/review.json --sid <sid>  # 9. 应用复核
#    → 你写 <jobdir>/card.json（定位 / 时间轴 / 术语表），然后：
$QZ assemble --sid <sid>                       # 10. 组装 note.md
$QZ write --sid <sid>                          # 11. 落盘
```

`<sid>` 形如 `youtube:dQw4w9WgXcQ`，由 `fetch` 输出里的 `source_id` 给出。
job 目录固定在 `~/.cache/video-extractor/jobs/<sid>/`，任何一步都能单独重跑。

## 2. 勘定（写 `/tmp/jieuding.json`）

读 `jieuding_pack.txt`（已含前段/4 段中段抽样/末段 + 元数据），只判断**结构**，不写时间：

```json
{
  "content_type": "lecture|interview|narrative|mixed|no-speech",
  "skeleton": "argument|topic|arc",
  "lang": "en",
  "units": [
    {"i": 1, "label": "开场：注意力为何难", "type": "论点", "start_anchor_id": "u0001"},
    {"i": 2, "label": "习惯回路的神经机制", "type": "论点", "start_anchor_id": "u0012"}
  ],
  "glossary": [{"src": "mindfulness", "zh": "正念", "src_lang": "en"}]
}
```

硬约束：

- `units` 数量 **∈ [3, 9]**。少于 3 或超过 9 会被拒。
- `start_anchor_id` 必须是**勘定包里出现过的 id**，且**严格递增**。
- `label` ≤12 字，是主题标签，不是句子。
- `type` ∈ `论点|事件|转折|铺垫`，决定**结论落到哪个区**：`事件` → 时间轴，`论点`/`转折` → 论点区。
- 纯音乐/无人声 → `content_type: "no-speech"` + `units: []`。
- 闲聊型内容 `skeleton: "topic"`，**不要硬造论点**。
- 跨语言时 `glossary` 必填；已收录术语在正文里必须照译。

## 3. 提炼（每段一个 `claims-<N>.json`）

读 `unit-N-pack.txt`，**只用该包内出现的 id**：

```json
[
  {
    "claim": "把注意力拉回呼吸看似简单，实际极其费力：静修中出汗、靠打盹撑住。",
    "evidence_ids": ["u0003", "u0005"],
    "evidence_zh": ["可我在冬季静修中出汗湿透T恤", "实际上这非常耗人"],
    "counter": null,
    "type": "论点"
  }
]
```

- `claim` **用中文写**（笔记正文是中文）；不要写成原文摘录。
- `evidence_ids` 必须来自该段 pack。**引用了段外的 id 会被整条丢弃**（这是故意的）。
- `evidence_zh` 可选，与 `evidence_ids` 一一对应，用于跨语言引用块的双语展示。
- `type` ∈ `论点|事件|转折|闲聊`。事件进时间轴气质，论点进论点区。
- 每段至少 1 条。整个单元拿不出主张时，给 1 条 `type: "闲聊"` 并直说内容性质，**不要编**。

## 4. 语义复核（写 `/tmp/review.json`）

`review-pack` 会为每条「论点」抽 **1 条原文**，问同一个问题：

> 这段原文是否支持这条结论？

```json
[{"claim_id": "c001", "supported": true}, {"claim_id": "c005", "supported": false}]
```

- 只判断"原文是否支持"，不判断"结论是否重要"。
- `false` 的结论会被标记 `low_confidence`，**不进"三条最硬结论"**，并在正文里带 `[需核实]`。
- 明显只沾边的（原文只提到半句）判 `false`，不要为了凑数判 `true`。

## 5. 卡片（写 `<jobdir>/card.json`）

```json
{
  "intro": "本视频是〈主持人〉与〈嘉宾〉围绕〈话题/现象〉进行的讨论。全视频有以下关键线索与论点：",
  "summary": "2–4 句的整段概括：从哪儿开场、中段怎么推进、最后落在哪里。",
  "timeline": [{"anchor_id": "u0035", "event": "转折：不再对抗大脑，改为给奖赏学习加一个扭转"}],
  "glossary": [{"src": "mindfulness", "zh": "正念"}]
}
```

- **`intro` 是固定句式**，必须照这个骨架写：`本视频是〈谁〉与〈谁〉围绕〈什么〉进行的讨论。全视频有以下关键线索与论点：`
  - 人名、机构名写准；嘉宾身份用一句话带上（如"加州大学伯克利分校认知神经科学家 Dr. X"）
  - 不写"内容充实/值得一看"这类判决词
- `summary`：整段概括，**按时间顺序讲全片走向**，不要罗列论点（论点区已经有了）。2–4 句。
- `timeline`：只写**事件、转折、演示、争论升级**。不复述论点区内容。
- `glossary`：跨语言时给。
- `top3` 可选：给 `["c003","c012"]` 就按此顺序取；不给则由脚本按规则挑（排除 low_confidence → 优先论点 → 优先多证据）。

> 起手句**不挂锚点**：它是结构性陈述，不是可追溯的主张。

## 6. 硬规则（违反即错）

| # | 规则 |
|---|---|
| 1 | **不输出任何时间码**。你只给 utterance id；时间码由脚本解析 |
| 2 | 引用的 id 必须在本段 pack 内。段外 id 会被丢弃 |
| 3 | `units` 必须在 [3,9]；边界首尾由脚本对齐到 0 与视频时长 |
| 4 | 术语表已收录的术语，正文必须用同一译法；未收录的允许自由 |
| 5 | 时间轴区**不复述**论点区（要说就用双链 `→ 见 [[#2. 论点标题]]`） |
| 6 | 不做"值不值得看"的判决，不写兴趣画像，不做全文翻译 |
| 7 | `content_type` 判 no-speech 就短路，不要为了凑段落硬切 |

## 7. 失败处置

| 症状 | 处理 |
|---|---|
| `[stage1.network]` | 让用户开代理；别重试硬等。代理端口变了改配置（见 §9） |
| `[stage1.live_stream]` | 直播中，拒绝；`was_live`（直播存档）正常处理 |
| `[stage1.members_only]` / `age_restricted` / `geo_blocked` | 如实告诉用户，不要绕 |
| `[stage2.quality]` 覆盖率低 | 字幕残缺 → 删掉 job 里的 `sub_path` 重跑 `clean` 走 ASR |
| `jieuding-check` 报 G1/G3/G5 | 按报错改 JSON：段数、递增、id 不存在 |
| `tiliang-check` 有 `invalid` 明细 | 那是你引用了别的段的 id，改到本段内的 id 重跑 |
| `all_failed: true` | 全部提炼失败。跑 `assemble` 会产出**降级笔记**（起手句 + 时间轴 + 底稿、无结论），这是预期行为，不要硬编 |

## 8. 产出长什么样

- frontmatter 用库里已有的**中文五键**（`日期`/`主题`/`标签`/`来源`/`关联`）并扩展，标签首项是 `视频笔记`（索引页按它筛）。
- 正文顺序固定：**`## 全片概括` → `## 时间轴` → `## 论点` → `## 术语表`（仅跨语言）→ `## 底稿`**。
  - `全片概括` = 起手句（固定句式）+ 概括段 + **三条最硬的结论**（带锚点）
  - `时间轴` 紧跟概括，承担"这段视频怎么走的"；**不再渲染结构地图表格**（`units` 仍是内部机制：3–9 段约束、单元过大时的二次切分都靠它，只是不出现在笔记里）
  - 事件型结论由脚本自动归入时间轴
- 锚点写法固定：`[12:34](https://youtu.be/<id>?t=754)`，**不带 `&list=`**。
- 重跑默认**另存** `… (重跑).md`，不动原文件；`write --force` 才覆盖。

## 9. 配置

默认 `config.default.json`；用户覆盖 `~/.config/video-extractor/config.json`。常用键：

| 键 | 默认 | 说明 |
|---|---|---|
| `proxy` | `http://127.0.0.1:7897` | 代理；`null` 走直连 |
| `vault_path` | **（必填，默认空）** | 你的 Obsidian 库根目录。支持空格/单引号，务必参数化传参 |
| `notes_dir` | `视频笔记` | 落盘子目录 |
| `sub_langs` | zh-Hans, zh-CN, zh-Hant, en, en-US, en-GB | 兜底白名单；**原语言优先** |

> 为什么原语言优先：实测英文 TED 演讲的 `subtitles` 里有 44 种人工翻译（含 `zh-CN`）。若按语种白名单取，会把中文译文当底稿，违反"底稿保留原语言"。

## 10. 回归（改代码后必跑）

```bash
./.venv/bin/python tests/run_tests.py               # 53 项契约断言
./.venv/bin/python scripts/extract.py corpus-check   # 冻结语料，只看 PASS/FAIL
```

`corpus-check` 读 `CORPUS.md`：`PASS` 是好的，`PENDING` 是**缺样本**（不是失败，按文档表格补齐即可），`SKIP` 表示该样本还没 fetch 过。

## 11. 自检（交付前逐条过）

1. `note.md` 里没有出现"值得一看/内容充实"这类判决词
2. 每条结论后面都能点开，且点开的内容确实在说这件事
3. 起手句是 `本视频是〈谁〉与〈谁〉围绕〈什么〉进行的讨论…` 这个句式，没写判决词
4. 时间轴在前、论点在后；论点区与时间轴区没有重复叙述同一件事
5. 跨语言视频有术语表，且正文译法一致
6. 没有把 `[需核实]` 的结论塞进三条最硬结论
