# video-extractor · 视频凝练器

把一个 YouTube 链接，变成一份**可追溯**的结构化中文笔记，直接落进 Obsidian。

> 市面上的工具解决的是「转写」，不是「读懂」。转写出来的文字再完整，你还是得逐句读完。
> 这个工具做的是：**把转写稿变成一份你愿意读、且敢相信的笔记**，并且每一句结论都能点回原片核对。

---

## 它解决什么

| 你会遇到的 | 本工具给的 |
|---|---|
| 一段短/长视频或一期播客，看到结论想核对，得从头拖进度条 | 每条结论后跟 `[12:34](https://youtu.be/…?t=754)`，点一下就跳到那一秒 |
| 摘要写得漂亮，但不知道是不是编的 | 模型**只准选句子编号**，时间码由代码查表——编不出锚点 |
| 笔记散落在各种 App 里，搜不到 | 落在你自己的 Obsidian，是全库可搜的 Markdown |
| 一堆工具都想替你判断"值不值得看" | 这个不做判决，只回答"这段内容里有什么、在哪一分钟" |

## 核心设计

**脚本做所有确定性的事，模型只做语义判断。**

| 谁 | 做什么 |
|---|---|
| 脚本 | 取数、清洗、断句、校验 id、统计时间、组装、落盘。**它算时间码** |
| 模型 | 只做三件语义判断：勘定（切几段）、提炼（每段主张）、复核（结论与原文对不对得上）。**只输出 utterance id** |

这条分工是整个项目的地基：**模型永远不写时间码**，锚点错误从"可能发生"变成"不可能发生"。

## 快速开始

```bash
git clone https://github.com/runlinxin-arch/video-extractor.git
cd video-extractor/extractor
bash setup_env.sh                      # 建 .venv（Python 3.12）+ pyyaml

# 配置你的 Obsidian 库路径（必填，不进仓库）
export VIDEO_EXTRACTOR_VAULT="/path/to/your/vault"
# 或写进 ~/.config/video-extractor/config.json
#   {"vault_path": "/path/to/your/vault"}

./.venv/bin/python scripts/extract.py preflight          # 探测 YouTube 可达性
./.venv/bin/python scripts/extract.py fetch "https://youtu.be/XXXX"
```

后续流程（清洗 → 勘定 → 提炼 → 复核 → 组装 → 落盘）见 [`extractor/SKILL.md`](extractor/SKILL.md)，那是给智能体照着跑的操作规范。

### 前置依赖

| 依赖 | 用途 | 必需 |
|---|---|---|
| Python 3.12 + [uv](https://github.com/astral-sh/uv) | 运行环境 | ✅ |
| [yt-dlp](https://github.com/yt-dlp/yt-dlp) | 取元数据与字幕/音频 | ✅ |
| node | yt-dlp 解 YouTube 的 JS 挑战 | 建议 |
| ffmpeg | DASH 音视频分离时合并 | ASR 时需要 |
| mlx-whisper | 无字幕时本地 ASR（Apple Silicon） | 可选 |

> 访问 YouTube 需要能用的出口。前言里的 `preflight` 会先探活，**失败立即退出而不是挂起**。

## 仓库结构

```
├── PRD.md         产品需求（定位、非目标、验收标准）
├── SPEC.md        工程规格（管线、契约、断言、失败处理）
├── REVIEW.md      审校记录：为什么长这样，以及实现期推翻过哪些规则
└── extractor/     ← 技能本体（DSH skill）
    ├── SKILL.md              智能体操作规范
    ├── CORPUS.md             冻结验收语料（10 个真实样本）
    ├── scripts/              9 个模块，CLI 12 个子命令
    ├── tests/                59 项回归断言 + 合成字幕夹具
    └── config.default.json
```

## 质量保证

```bash
cd extractor
./.venv/bin/python tests/run_tests.py                  # 59 项契约断言
./.venv/bin/python scripts/extract.py corpus-check     # 冻结语料，只看 PASS/FAIL
```

`CORPUS.md` 里冻结了真实样本（极短/单论点/长访谈/无字幕 ASR/中文原生），改代码前后各跑一次。**没有冻结语料，"锚点命中率 ≥90%"这类指标无法复现。**

## 已知限制

- **只支持 YouTube 单条视频**（B 站、本地文件、播放列表在 v0.2）
- **中文无标点字幕**会让断句退化成按字数硬切（`quality.json` 会标 `no_punctuation`）
- 长访谈的提炼耗时取决于模型侧，脚本部分只占秒级
- 纯音乐 / 无人声会产出 `no-speech` 短路结果

## License

[MIT](LICENSE)
