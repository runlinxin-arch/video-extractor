#!/usr/bin/env python3
"""回归测试：把 SPEC 里的硬规则变成断言。

跑法： .venv/bin/python tests/run_tests.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import assemble as asm  # noqa: E402
import config  # noqa: E402
import fetch as fetcher  # noqa: E402
import jieuding  # noqa: E402
import subs  # noqa: E402
import tiliang  # noqa: E402
import writer  # noqa: E402

sys.path.insert(0, str(ROOT / "tests" / "fixtures"))
import gen_vtt  # noqa: E402
DURATION = 4562.0
PASS = 0
FAIL = []
CFG = config.load()


def check(name: str, cond: bool, detail: str = ""):
    global PASS
    if cond:
        PASS += 1
    else:
        FAIL.append(f"{name} {detail}")
        print(f"  ✗ {name} {detail}")


def case(title: str):
    print(f"\n[{title}]")


# ---------------------------------------------------------------- 字幕清洗
case("字幕清洗 / utterance 契约（合成夹具）")
with tempfile.TemporaryDirectory() as _td:
    FIXTURE = Path(_td) / "synthetic.en.vtt"
    FIXTURE.write_text(gen_vtt.build(), encoding="utf-8")
    cues = subs.parse_captions(str(FIXTURE))
    check("解析出 cue", len(cues) >= 2500, f"got {len(cues)}")
    text, times, endtimes, speakers, stats = subs.assemble(cues, CFG)
    utts = subs.build_utterances(text, times, endtimes, speakers, "en", CFG)
    rep = subs.quality_report(utts, stats, 3600.0, CFG)

    check("去滚动生效（原始字符远多于合并后）", rep["dedup_ratio"] > 2.0, str(rep["dedup_ratio"]))
    check("覆盖率 >= 0.95", rep["coverage"] >= 0.95, str(rep["coverage"]))
    check("字符数在合理区间", 60000 < rep["chars"] < 110000, str(rep["chars"]))
    check("质量断言通过", rep["ok"], str(rep["problems"]))
    check("去滚动比贴近真实（2–4）", 2.0 <= rep["dedup_ratio"] <= 4.0, str(rep["dedup_ratio"]))
    check("speaker 有被填充（>> 标记）", any(u["speaker"] for u in utts))

    # U1–U6
    try:
        subs.assert_contract(utts)
        check("U1–U5 契约断言", True)
    except ValueError as e:
        check("U1–U5 契约断言", False, str(e))

    check("没有内嵌 <c> 残留", not any("<c>" in u["text"] for u in utts))
    check("没有 >> 残留", not any(">>" in u["text"] for u in utts))
    check("每句都不为空", all(u["text"].strip() for u in utts))
    lens = sorted(len(u["text"]) for u in utts)
    check("单句长度 p99 <= 220", lens[int(len(lens) * 0.99)] <= 220, str(lens[int(len(lens) * 0.99)]))


# ---------------------------------------------------------------- 中文切分
case("中文 / 无标点硬切不劈英文词")
zh = "。".join([]) or ("背景音乐对于视频来说很重要而对于Vlog来说可以找到适合的音乐"
                      "也可以让你的Vlog真色不少今天我会从三个方面聊一聊这件事请继续往下看")
cfg2 = dict(CFG); cfg2["utterance_max_zh"] = 30; cfg2["sentence_gap_s"] = 99
text = zh; times = [i * 0.2 for i in range(len(text))]
utts_zh = subs.build_utterances(text, times, times[:], [0] * len(text), "zh", cfg2)
joined = "".join(u["text"] for u in utts_zh)
check("中文切分后拼回原文", joined == text.replace(" ", ""), joined[:40])
check("不出现孤立的大写 V 结尾", not any(u["text"].endswith("V") for u in utts_zh))
check("每句 <= 上限（允许微溢）", all(len(u["text"]) <= 30 + 2 for u in utts_zh),
      str([len(u["text"]) for u in utts_zh]))

# ---------------------------------------------------------------- 勘定
case("勘定 / tiling 与约束")
utts = [{"id": f"u{i:04d}", "start": i * 10.0, "end": i * 10.0 + 9.0, "text": f"s{i}",
         "lang": "en", "speaker": None} for i in range(1, 21)]
job = {"duration": 200.0, "title": "t", "lang": "en", "transcript_source": "official"}

ok = {"content_type": "lecture", "skeleton": "argument", "lang": "en",
      "units": [{"i": 1, "label": "a", "start_anchor_id": "u0001"},
                {"i": 2, "label": "b", "start_anchor_id": "u0007"},
                {"i": 3, "label": "c", "start_anchor_id": "u0014"}],
      "glossary": []}
res = jieuding.validate(ok, utts, job, CFG)
check("合法勘定无错", not res["errors"], str(res["errors"]))
check("tiling 首段从 0", res["units"][0]["start"] == 0.0)
check("tiling 末段到 duration", abs(res["units"][-1]["end"] - 200.0) < 1e-6)
check("tiling 无空隙", all(res["units"][i]["end"] == res["units"][i + 1]["start"]
                          for i in range(len(res["units"]) - 1)))

bad = dict(ok, units=ok["units"][:2])
check("G1 拒绝 2 段", bool(jieuding.validate(bad, utts, job, CFG)["errors"]))

bad2 = dict(ok, units=[{"i": 1, "label": "a", "start_anchor_id": "uZZZZ"},
                       {"i": 2, "label": "b", "start_anchor_id": "u0007"},
                       {"i": 3, "label": "c", "start_anchor_id": "u0014"}])
check("G5 拒绝不存在的 id", any("G5" in e for e in jieuding.validate(bad2, utts, job, CFG)["errors"]))

ns = {"content_type": "no-speech", "skeleton": "topic", "lang": "und", "units": [], "glossary": []}
check("no-speech 允许空 units", not jieuding.validate(ns, utts, job, CFG)["errors"])

# ---------------------------------------------------------------- 提炼校验
case("提炼 / id 校验")
units = res["units"]
with tempfile.TemporaryDirectory() as td:
    td = Path(td)
    (td / "claims-1.json").write_text(json.dumps([
        {"claim": "对的主张", "evidence_ids": ["u0001"], "type": "论点"},
        {"claim": "伪造的锚点", "evidence_ids": ["u0099"], "type": "论点"},
    ], ensure_ascii=False), encoding="utf-8")
    (td / "claims-2.json").write_text(json.dumps([
        {"claim": "二段主张", "evidence_ids": ["u0007", "u0008"], "type": "事件"}],
        ensure_ascii=False), encoding="utf-8")
    files = {1: td / "claims-1.json", 2: td / "claims-2.json"}
    out = tiliang.check_claims(files, units, utts)
    check("剔除段外 id", out["stats"]["invalid_ids"] == 1, str(out["stats"]["invalid_ids"]))
    check("保留合法条目", out["stats"]["kept"] == 2, str(out["stats"]["kept"]))
    check("缺失单元的段记为失败", 3 in out["stats"]["units_failed"])
    check("claim_id 连续", [c["claim_id"] for c in out["claims"]] == ["c001", "c002"])

# ---------------------------------------------------------------- 笔记结构
case("笔记结构 / 全片概括 + 时间轴前置")
with tempfile.TemporaryDirectory() as td:
    td = Path(td)
    utts = [{"id": f"u{i:04d}", "start": i * 10.0, "end": i * 10.0 + 9.0,
             "text": f"sentence {i}", "lang": "en", "speaker": None} for i in range(1, 13)]
    (td / "utterances.json").write_text(json.dumps(utts, ensure_ascii=False), encoding="utf-8")
    job = {"source_id": "youtube:x", "video_id": "x", "title": "T", "duration": 120.0,
           "lang": "en", "transcript_source": "auto", "source_url": "u",
           "_jobdir": str(td), "_date": "2026-01-01", "_now": "2026-01-01T00:00:00+0800"}
    units = [{"i": 1, "label": "甲", "type": "论点", "start": 0.0, "end": 60.0},
             {"i": 2, "label": "乙", "type": "事件", "start": 60.0, "end": 120.0}]
    claims = [
        {"claim_id": "c001", "unit": 1, "claim": "第一条主张", "evidence_ids": ["u0002", "u0003"],
         "counter": None, "type": "论点", "low_confidence": False},
        {"claim_id": "c002", "unit": 2, "claim": "一件事件", "evidence_ids": ["u0009"],
         "counter": None, "type": "事件", "low_confidence": False},
        {"claim_id": "c003", "unit": 1, "claim": "存疑的主张", "evidence_ids": ["u0005"],
         "counter": None, "type": "论点", "low_confidence": True},
    ]
    card = {"intro": "本视频是 A 与 B 围绕某话题进行的讨论。全视频有以下关键线索与论点：",
            "summary": "概括段。", "timeline": [{"anchor_id": "u0001", "event": "开场"}],
            "glossary": [{"src": "x", "zh": "某"}]}
    fm, body = asm.render(job, units, claims, card, CFG, {"coverage": 0.9},
                          card["glossary"], "视频笔记/底稿/d")
    i_ov = body.index("## 全片概括")
    i_tl = body.index("## 时间轴")
    i_arg = body.index("## 论点")
    check("章节顺序 全片概括→时间轴→论点", i_ov < i_tl < i_arg, f"{i_ov},{i_tl},{i_arg}")
    check("不再渲染结构地图表格", "| 时间段 | 主题 | 类型 |" not in body)
    check("起手句在概括区首句", 0 <= body.index("本视频是 A 与 B") < body.index("概括段。"))
    check("概括段被渲染", "概括段。" in body)
    check("三条结论紧跟起手句与概括", body.index("三条最硬的结论") < i_tl)
    check("事件型结论进时间轴", "一件事件" in body[i_tl:i_arg])
    check("[需核实] 渲染到论点区", "存疑的主张 \`[需核实]\`" in body or "存疑的主张 `[需核实]`" in body)

# ---------------------------------------------------------------- 锚点
case("锚点规范")
md = asm.md_anchor("abc12345678", 754.2)
check("锚点是可点击链接", md.startswith("[12:34](https://youtu.be/abc12345678?t=754)"), md)
md2 = asm.md_anchor("abc12345678", 3725)
check("超 1 小时用 H:MM:SS", md2.startswith("[1:02:05]"), md2)
check("不含 &list=", "&list=" not in md2 and "&index=" not in md2)

# ---------------------------------------------------------------- 文件名与落盘
case("文件名 / 幂等 / 另存")
check("净化非法字符", writer.sanitize('a/b:c*d?"e<f>g|h') == "a-b-c-d--e-f-g-h",
      writer.sanitize('a/b:c*d?"e<f>g|h'))
check("长度上限", len(writer.sanitize("x" * 200)) <= 81)
check("去掉首尾点与空格", writer.sanitize("  .abc.  ") == "abc")

with tempfile.TemporaryDirectory() as td:
    vault = Path(td)
    md_text = asm.dump_note({"日期": "2026-01-01", "source_id": "youtube:x",
                             "首次处理": "2026-01-01"}, "# t\n\n正文 A\n")
    r1 = writer.save(vault, "视频笔记", md_text, {"a": 1}, "标题", "youtube:x")
    check("首次 created", r1["action"] == "created", r1["action"])
    check("底稿落在 底稿/ 普通目录", (vault / "视频笔记" / "底稿").is_dir())
    r2 = writer.save(vault, "视频笔记", md_text, {"a": 1}, "标题", "youtube:x")
    check("同内容幂等", r2["action"] == "idempotent", r2["action"])
    md_text2 = asm.dump_note({"日期": "2026-01-01", "source_id": "youtube:x"}, "# t\n\n正文 B\n")
    r3 = writer.save(vault, "视频笔记", md_text2, {"a": 2}, "标题", "youtube:x")
    check("内容变了另存", r3["action"] == "rerun_new", r3["action"])
    check("原文件还在", Path(r1["path"]).exists())
    r4 = writer.save(vault, "视频笔记", md_text2, {"a": 2}, "标题", "youtube:x", force=True)
    check("--force 覆盖", r4["action"] == "overwritten", r4["action"])
    check("覆盖后原文件名不变", r4["path"] == r1["path"])
    txt_after = Path(r4["path"]).read_text(encoding="utf-8")
    fm_after = writer.parse_fm(txt_after)[0]
    check("覆盖时保留首次处理", str(fm_after.get("首次处理", "")) == "2026-01-01")
    check("覆盖后无重复键", txt_after.count("首次处理") == 1 and txt_after.count("最近处理") == 1)

# ---------------------------------------------------------------- 语言选择
case("语种选择 / 翻译陷阱")
check("识别 zh-Hans-en 为翻译变体", fetcher.is_translation_variant("zh-Hans-en"))
check("识别 af-en 为翻译变体", fetcher.is_translation_variant("af-en"))
check("zh-Hans 不是翻译变体", not fetcher.is_translation_variant("zh-Hans"))
check("en-GB 不是翻译变体", not fetcher.is_translation_variant("en-GB"))

info = {"language": "en",
        "subtitles": {"en": [{}], "zh-CN": [{}]},
        "automatic_captions": {"en": [{}], "zh-Hans-en": [{}]}}
lang, src, is_orig = fetcher.pick_language(info, CFG)
check("原语言优先（不选 zh-CN）", lang == "en" and is_orig, f"{lang}/{is_orig}")

info2 = {"language": "zh", "subtitles": {}, "automatic_captions": {"zh-Hans": [{}], "en": [{}]}}
lang2, _, orig2 = fetcher.pick_language(info2, CFG)
check("中文原语言选中 zh-Hans", lang2 == "zh-Hans" and orig2, str(lang2))

check("URL 解析 watch?v=", fetcher.video_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ") == "dQw4w9WgXcQ")
check("URL 解析 youtu.be", fetcher.video_id("https://youtu.be/dQw4w9WgXcQ?t=30") == "dQw4w9WgXcQ")
check("URL 解析 shorts", fetcher.video_id("https://youtube.com/shorts/dQw4w9WgXcQ") == "dQw4w9WgXcQ")

# ---------------------------------------------------------------- ASR 错误分类
case("ASR / 失败分类")
try:
    import asr
except Exception:  # noqa: BLE001
    asr = None
if asr is not None:
    import types
    fake = types.ModuleType("mlx_whisper")

    def _boom(*a, **k):
        raise OSError("ConnectError: [Errno 61] Connection refused")

    fake.transcribe = _boom
    real = sys.modules.get("mlx_whisper")
    sys.modules["mlx_whisper"] = fake
    try:
        asr.transcribe("/tmp/x.m4a", CFG)
        check("离线时应抛 ASRError", False)
    except asr.ASRError as e:
        check("离线 → model_unavailable", e.code == "model_unavailable", e.code)
        check("错误里带 hint", bool(e.hint))
    finally:
        if real is not None:
            sys.modules["mlx_whisper"] = real
        else:
            sys.modules.pop("mlx_whisper", None)

# ---------------------------------------------------------------- 汇总
print(f"\n{'=' * 50}")
print(f"通过 {PASS} 项，失败 {len(FAIL)} 项")
for f in FAIL:
    print("  ✗", f)
sys.exit(1 if FAIL else 0)
