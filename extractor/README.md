# video-extractor · 视频凝练器

一个 YouTube 单条视频 → 一份**可追溯**的中文笔记，写进 Obsidian。

- 设计规格：[`../SPEC.md`](../SPEC.md)
- 产品需求：[`../PRD.md`](../PRD.md)
- 审校记录（为什么长这样）：[`../REVIEW.md`](../REVIEW.md)

## 为什么是"脚本 + 智能体"分工

步骤是固定的（取数→清洗→勘定→提炼→落盘），不需要自主规划；但勘定和提炼需要语义判断。
所以：**脚本做所有确定性的事**（含时间码），**智能体只做语义判断**（只输出 utterance id）。

这条分工是产品的地基：**模型永远不写时间码**，锚点错误从"可能发生"变成"不可能发生"。

## 快速开始

```bash
bash setup_env.sh              # 建 .venv + pyyaml
./.venv/bin/python scripts/extract.py preflight
./.venv/bin/python scripts/extract.py fetch "https://www.youtube.com/watch?v=XXXX"
```

完整流程见 `SKILL.md`（智能体照着跑的那份），或 `scripts/extract.py --help`。

## 目录

```
SKILL.md              ← 智能体操作规范（勘定/提炼/复核的输出格式与硬规则）
config.default.json   ← 默认配置（代理、库路径、阈值）
scripts/
  extract.py          ← CLI 入口，11 个子命令
  fetch.py            ← 预检 / yt-dlp / 语种选择（原语言优先）
  subs.py             ← 字幕解析 → 重叠合并 → 断句 → utterance 契约
  asr.py              ← mlx-whisper 兜底
  jieuding.py         ← 勘定抽样包 + 校验 + tiling
  tiliang.py          ← 分块提炼打包 + id 校验 + 复核队列
  assemble.py         ← frontmatter（中文五键）+ 正文组装 + 锚点渲染
  writer.py           ← 文件名净化 / 幂等 / 另存 / --force
tests/
  run_tests.py        ← 46 项回归断言
  fixtures/           ← 真实字幕样本（76 分钟，4,053 cue）
```

## 几个不看代码就会踩的坑

| 坑 | 事实 |
|---|---|
| 自动字幕是**滚动窗口** | 实测 4,053 cue 里 2,032 条是 ≤50ms 碎片、1,952 条相邻前缀重复，直接拼接文本膨胀 **202%**。必须重叠合并，**不能按"时长太短就丢"**——10ms 的 cue 常常承载新词 |
| 人工字幕可能是**翻译稿** | TED 的 `subtitles` 里有 44 种人工翻译。按语种白名单取会选到 `zh-CN`，把中文译文当底稿。所以**原语言优先**，非原语言就改走 ASR |
| `--sub-langs` 不设白名单会混入垃圾 | yt-dlp 把 `live_chat` 也注册成一种字幕（`ext=json`），弹幕/聊天记录会被当转写 |
| 代理是**按需启用**的 | 前置探活失败必须立即退出，不能挂起（实测盲跑会挂 150 秒） |
| 库路径可能含空格与引号 | 一律参数化传参，不要拼 shell 字符串（用户名里带空格或引号、或本地化目录名，都会把 shell 命令劈开） |

## 测试

```bash
./.venv/bin/python tests/run_tests.py     # 单元/契约回归（53 项）
./.venv/bin/python scripts/extract.py corpus-check   # 验收语料（CORPUS.md）
```

`run_tests.py` 覆盖：清洗契约、tiling 精确性、id 校验、锚点格式、文件名净化、幂等/另存/覆盖、语种选择与翻译陷阱、章节结构、ASR 失败分类。

`corpus-check` 拿 `CORPUS.md` 里**冻结的 10 个真实样本**重跑断言：清洗质量、utterance 契约、锚点 URL 形态、章节顺序与位置、`[需核实]` 渲染、frontmatter 完整性。改代码前后各跑一次，**只看 PASS/FAIL**——没有它，"锚点命中率 ≥90%"这类指标无法复现。
