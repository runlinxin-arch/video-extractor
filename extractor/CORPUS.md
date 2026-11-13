# CORPUS · 验收语料

> 冻结的验收样本。**改代码前后各跑一次，只看 PASS/FAIL。**
> 没有这份清单，SPEC §16 的"锚点命中率 ≥90%"和 PRD §7.1 的指标都无法复现——也就无法判断"做完"。

跑法：

```bash
./.venv/bin/python scripts/extract.py corpus-check
```

退出码 0 = 全通过；非 0 = 有失败项（会列出是哪一条、哪一项）。

---

## 两层验收

| 层 | 覆盖 | 成本 | 频率 |
|---|---|---|---|
| **A · 自动断言**（本文档机器块驱动） | 清洗质量、utterance 契约、锚点 URL 形态、章节顺序、`[需核实]` 渲染、frontmatter 完整性、**压缩比**（≥40 分钟 ≤0.35；<40 分钟 ≤3,000 中文字）、禁用章节（如中文不该有术语表） | 秒级 | 每次改代码 |
| **B · 人工核锚**（手工，本节表格） | 锚点"位置正确 + 内容相关"、术语一致、卡片可读性 | 每份 10–15 分钟 | 每季度一次 |

**为什么分两层**：一条 2 小时视频的转写有 13.5 万字符，全套 10 个都做人工核锚点不现实。日常回归只跑 A，B 只看 2–3 个样本。

---

## 已冻结的样本

| # | 样本 | 时长 | 字幕来源 | 语言 | 用途 | 状态 |
|---|---|---|---|---|---|---|
| 1 | [Me at the zoo](https://youtu.be/jNQXAC9IVRw) | 0:19 | 人工 | en | 极小样本：验管线不崩、边界值（<20s） | ✅ 已跑 |
| 2 | [A Simple Way to Break a Bad Habit](https://youtu.be/-moW9jvvMr4) | 9:25 | 人工 | en | 单论点结构（`argument`）、TED 多语言翻译陷阱 | ✅ 已跑 |
| 3 | [Phone Addiction Scientist](https://youtu.be/yePLuwL3FbM) | 2:14:54 | 自动 | en | 长访谈：滚动字幕碎片、9 段、广告段、57 条结论 | ✅ 已跑 |
| 4 | [如何给VLOG找背景音乐](https://www.youtube.com/watch?v=JtotxvdS5Qc) | 13:35 | **无字幕 → ASR** | zh | ASR 兜底：81 条 utterance、无标点、按 60 字硬切、18.6× 实时 | ✅ 已跑 |
| 5 | [如何说话不卡壳](https://www.youtube.com/watch?v=_ZFO4Qm0QT8) | 11:38 | 人工 | zh | 中文原生：**无标点**字幕、60 字硬切、术语表须省略、笔记 1.13× 转写 | ✅ 已跑 |
| 6 | 待定 | — | 自动 | — | **`was_live`** 直播存档：验不被误拒 | ⬜ 缺样本 |
| 7 | 待定 | — | 任意 | — | **真正的水视频**：US-5 注水检验 | ⬜ 缺样本 |
| 8 | 待定 | — | 自动 | — | 多说话人（转写里出现 `>>`） | ⬜ 缺样本 |
| 9 | 待定 | — | 任意 | — | 超长（>3h）：验分段与 unit 二次切分 | ⬜ 缺样本 |
| 10 | 待定 | — | **只有翻译字幕** | 非 en | 验"原语言优先"：应改走 ASR 而不是拿译文当底稿 | ⬜ 缺样本 |

### 缺的 7 个怎么挑

- ~~#4 无字幕~~ 已冻结
- **#6 直播存档**：发布会、直播访谈的回放（`live_status: was_live`）
- **#7 水视频**：标题党、重复灌水、纯闲聊 40 分钟以上
- **#8 多说话人**：两个以上讲话者的圆桌/播客
- **#10 翻译字幕**：非英语频道里有官方多语字幕的（TED、大厂发布会）

填进下面的机器块后，跑一次 `fetch` + `clean` 就会自动纳入回归。

---

## 人工核锚记录（B 层）

每季度抽 2–3 份，每份随机 3 个锚点，逐一点开原片核对：

| 日期 | 样本 | 抽出锚点 | 命中 | 备注 |
|---|---|---|---|---|
| — | — | — | — | 尚未开始 |

命中标准（两条都要满足）：
1. **位置正确**：跳过去之后，那句话在前后 10 秒内出现
2. **内容相关**：跳过去的上下文，确实在讲这条结论说的那件事

---

## 机器可读块（脚本读这一段，勿改结构）

```json
{
  "version": 1,
  "entries": [
    {
      "id": "tiny-manual",
      "url": "https://youtu.be/jNQXAC9IVRw",
      "duration_s": 19,
      "subs": "official",
      "lang": "en",
      "why": "极小样本：验管线不崩",
      "status": "frozen",
      "expect": {
        "utterances": [1, 30],
        "coverage_min": 0.85,
        "chars_per_min": [100, 2000],
        "note": null
      }
    },
    {
      "id": "ted-manual-argument",
      "url": "https://youtu.be/-moW9jvvMr4",
      "duration_s": 565,
      "subs": "official",
      "lang": "en",
      "why": "单论点结构 + TED 翻译字幕陷阱",
      "status": "frozen",
      "expect": {
        "utterances": [60, 140],
        "coverage_min": 0.9,
        "chars_per_min": [600, 1200],
        "note": {
          "units": [3, 9],
          "claims": [10, 40],
          "sections": ["全片概括", "时间轴", "论点", "术语表", "底稿"]
        }
      }
    },
    {
      "id": "podcast-auto-long",
      "url": "https://youtu.be/yePLuwL3FbM",
      "duration_s": 8094,
      "subs": "auto",
      "lang": "en",
      "why": "长访谈：滚动字幕碎片 + 9 段 + 广告段",
      "status": "frozen",
      "expect": {
        "utterances": [2500, 3600],
        "coverage_min": 0.95,
        "chars_per_min": [700, 1200],
        "note": {
          "units": [3, 9],
          "claims": [40, 80],
          "sections": ["全片概括", "时间轴", "论点", "术语表", "底稿"]
        }
      }
    },
    {
      "id": "asr-no-subs",
      "url": "https://www.youtube.com/watch?v=JtotxvdS5Qc",
      "duration_s": 815,
      "subs": "asr",
      "lang": "zh",
      "why": "无字幕 → ASR 兜底：中文口播、无标点、按 60 字硬切",
      "status": "frozen",
      "expect": {
        "transcript_source": "asr",
        "lang": "zh",
        "utterances": [60, 110],
        "coverage_min": 0.9,
        "chars_per_min": [200, 500],
        "note": null
      }
    },
    {
      "id": "zh-native",
      "url": "https://www.youtube.com/watch?v=_ZFO4Qm0QT8",
      "duration_s": 698,
      "subs": "official",
      "lang": "zh",
      "why": "中文原生：无标点字幕（461 个 1–2 字 cue）、60 字硬切、术语表须省略",
      "status": "frozen",
      "expect": {
        "transcript_source": "official",
        "lang": "zh",
        "utterances": [50, 90],
        "coverage_min": 0.9,
        "chars_per_min": [250, 450],
        "note": {
          "units": [3, 9],
          "claims": [12, 30],
          "sections": ["全片概括", "时间轴", "论点", "底稿"],
          "forbidden_sections": ["术语表"],
          "max_body_chars": 6000
        }
      }
    },
    {
      "id": "live-archive",
      "url": null,
      "duration_s": null,
      "subs": "auto",
      "lang": null,
      "why": "was_live 直播存档：不得被误拒",
      "status": "pending",
      "expect": {}
    },
    {
      "id": "water-video",
      "url": null,
      "duration_s": null,
      "subs": "auto",
      "lang": null,
      "why": "US-5 注水检验",
      "status": "pending",
      "expect": {}
    },
    {
      "id": "multi-speaker",
      "url": null,
      "duration_s": null,
      "subs": "auto",
      "lang": null,
      "why": "多说话人（>> 标记）",
      "status": "pending",
      "expect": {}
    },
    {
      "id": "ultra-long",
      "url": null,
      "duration_s": null,
      "subs": "auto",
      "lang": null,
      "why": ">3h：分段与 unit 二次切分",
      "status": "pending",
      "expect": {}
    },
    {
      "id": "translated-subs-only",
      "url": null,
      "duration_s": null,
      "subs": "official",
      "lang": "non-orig",
      "why": "只有翻译字幕时须改走 ASR",
      "status": "pending",
      "expect": {}
    }
  ]
}
```
