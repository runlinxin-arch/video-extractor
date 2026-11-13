"""Stage 5：组装 markdown（frontmatter 中文五键 + 正文骨架）。"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import yaml

TYPE_MAP = {"论点": "论点", "事件": "事件", "转折": "转折", "闲聊": "铺垫"}


def fmt_ts(sec: float) -> str:
    s = int(sec)
    if s >= 3600:
        return f"{s // 3600}:{(s % 3600) // 60:02d}:{s % 60:02d}"
    return f"{s // 60:02d}:{s % 60:02d}"


def md_anchor(video_id: str, sec: float) -> str:
    """SPEC §8：唯一锚点写法。剔除 &list= / &index=。"""
    return f"[{fmt_ts(sec)}](https://youtu.be/{video_id}?t={int(sec)})"


def _unit_type(unit: dict, claims: list[dict]) -> str:
    explicit = (unit.get("type") or "").strip()
    if explicit in TYPE_MAP or explicit in set(TYPE_MAP.values()):
        return TYPE_MAP.get(explicit, explicit)
    types = [c["type"] for c in claims if c["unit"] == unit["i"]]
    if not types:
        return "铺垫"
    best = max(set(types), key=types.count)
    return TYPE_MAP.get(best, "铺垫")


QUOTE_MAX = 110      # 单条引文截断长度
MAX_QUOTES = 1       # 每条结论最多展示几条引文（2 条会让短内容比原文还长）
EXPAND_LIMIT = 24    # 论点区展开上限，超出者只给主张+锚点


def _clip(text: str, n: int = QUOTE_MAX) -> str:
    t = " ".join(text.split())
    return t if len(t) <= n else t[: n - 1] + "…"


def _title_of(claim: dict) -> str:
    t = (claim.get("title") or "").strip()
    if t:
        return t
    text = claim["claim"].strip()
    for sep in "，,。.；;：:":
        if sep in text[:24]:
            return text.split(sep)[0][:20]
    return text[:20]


def transcript_quality(job: dict, coverage: float) -> str:
    src = job.get("transcript_source")
    if src == "official" and coverage >= 0.9:
        return "high"
    if src in ("official", "auto") and coverage >= 0.7:
        return "medium"
    return "low"


def render(job: dict, units: list[dict], claims: list[dict], card: dict, cfg: dict,
           quality: dict, glossary: list[dict], draft_rel: str) -> tuple[dict, str]:
    vid = job["video_id"]
    dur = float(job["duration"])
    idmap = {}
    for p in [Path(job["_jobdir"]) / "utterances.json"]:
        if p.exists():
            for u in json.loads(p.read_text(encoding="utf-8")):
                idmap[u["id"]] = u
    src_label = {"official": "人工字幕", "auto": "自动字幕", "asr": "本地 ASR"}.get(
        job.get("transcript_source") or "", "未知来源")
    warn = "" if job.get("transcript_source") == "official" else \
        "（专有名词可能有误，关键结论请点回原片核对）"

    lines: list[str] = []
    lines.append(f"# {job['title']}")
    lines.append("")
    lines.append(f"> 来源：YouTube · {fmt_ts(dur)} · 转录：{src_label}{warn}")
    if job.get("was_live"):
        lines.append("> 注：原始为直播存档。")
    lines.append("")

    # ---- 全片概括 ----
    lines.append("## 全片概括")
    lines.append("")
    pos = card.get("positioning") or {}
    intro = (card.get("intro") or pos.get("text") or "").strip() or "（未给出概括）"
    # 起手句是结构性陈述，不挂锚点；锚点留在三条结论与论点区
    lines.append(intro)
    lines.append("")
    summary = (card.get("summary") or "").strip()
    if summary:
        lines.append(summary)
        lines.append("")

    top3 = card.get("top3")
    if top3:
        chosen = [c for c in claims if c["claim_id"] in set(top3)][:3]
    else:
        from tiliang import pick_top3
        chosen = pick_top3(claims)
    lines.append("**三条最硬的结论**")
    if chosen:
        for n, c in enumerate(chosen, start=1):
            u = idmap.get(c["evidence_ids"][0])
            a = (" " + md_anchor(vid, u["start"])) if u else ""
            flag = " `[需核实]`" if c["low_confidence"] else ""
            lines.append(f"{n}. {c['claim']}{a}{flag}")
    else:
        lines.append("1. （本片提炼失败或内容不构成结论，见底部说明）")
    lines.append("")

    # ---- 时间轴 ----
    lines.append("## 时间轴")
    lines.append("")
    tl = card.get("timeline") or []
    for item in tl:
        aid = item.get("anchor_id")
        ev = (item.get("event") or "").strip()
        u = idmap.get(aid)
        when = md_anchor(vid, u["start"]) if u else ""
        lines.append(f"- {when} {ev}")
    # 事件型结论也归时间轴（否则它们会从笔记里消失）
    event_claims = [c for c in claims if c["type"] == "事件"]
    for c in event_claims:
        u = idmap.get(c["evidence_ids"][0])
        when = md_anchor(vid, u["start"]) if u else ""
        lines.append(f"- {when} {c['claim']}")
    if not tl and not event_claims:
        lines.append("（无显著事件/转折）")
    lines.append("")

    # ---- 论点 ----
    lines.append("## 论点")
    lines.append("")
    arg_claims = [c for c in claims if c["type"] in ("论点", "转折")]
    if not arg_claims:
        lines.append("（本片为话题型/叙事型内容，无独立论点；请看时间轴）")
        lines.append("")
    for n, c in enumerate(arg_claims, start=1):
        u0 = idmap.get(c["evidence_ids"][0])
        head_anchor = (" " + md_anchor(vid, u0["start"])) if u0 else ""
        flag = " `[需核实]`" if c["low_confidence"] else ""
        if n > EXPAND_LIMIT:
            lines.append(f"- {c['claim']}{head_anchor}{flag}")
            continue
        lines.append(f"### {n}. {_title_of(c)}")
        lines.append(f"- **主张**：{c['claim']}{flag}")
        zh = c.get("evidence_zh") or []
        for k, eid in enumerate(c["evidence_ids"][:MAX_QUOTES]):
            u = idmap.get(eid)
            if not u:
                continue
            rel = "**证据**" if k == 0 else "　　↳"
            lines.append(f"- {rel}：{md_anchor(vid, u['start'])} \"{_clip(u['text'])}\"")
            if k == 0 and zh and zh[0]:
                lines.append(f"  - *译*：{zh[0]}")
        if c.get("counter"):
            lines.append(f"- **保留/反例**：{c['counter']}")
        lines.append("")
    if len(arg_claims) > EXPAND_LIMIT:
        lines.append(f"> 其余 {len(arg_claims) - EXPAND_LIMIT} 条结论已省略引文，只保留主张与锚点。")
        lines.append("")

    # ---- 术语表（仅跨语言时）----
    if glossary:
        lines.append("## 术语表")
        lines.append("")
        lines.append("| 原文 | 统一译法 |")
        lines.append("|---|---|")
        for g in glossary:
            lines.append(f"| {g.get('src', '')} | {g.get('zh', '')} |")
        lines.append("")

    # ---- 底稿 ----
    lines.append("## 底稿")
    lines.append("")
    lines.append(f"[[{draft_rel}]]")
    lines.append("")
    body = "\n".join(lines)

    fm = {
        "日期": card.get("date") or job["_date"],
        "主题": job["title"],
        "标签": ["视频笔记", "YouTube", _lang_tag(job.get("lang")), _len_tag(dur)],
        "来源": f"YouTube · {fmt_ts(dur)} · {src_label} · {job['source_url']}",
        "关联": [f"[[{draft_rel}]]"],
        "source_id": job["source_id"],
        "source_url": job["source_url"],
        "duration": int(dur),
        "lang": job.get("lang") or "und",
        "transcript_source": job.get("transcript_source") or "asr",
        "transcript_quality": transcript_quality(job, quality.get("coverage", 0.0)),
        "units": len(units),
        "anchors": len({c["evidence_ids"][0] for c in claims if c["evidence_ids"]}),
        "glossary_count": len(glossary),
        "首次处理": job["_now"],
        "最近处理": job["_now"],
        "流水线": cfg.get("pipeline_version", "0.2.0"),
        "降级": bool(card.get("degraded")),
    }
    if card.get("degraded"):
        fm["降级原因"] = card.get("degraded_reason") or "unknown"
    return fm, body


def _lang_tag(lang: str | None) -> str:
    if not lang:
        return "未知语言"
    if lang.startswith("zh"):
        return "中文"
    if lang.startswith("en"):
        return "英文"
    return lang


def _len_tag(dur: float) -> str:
    m = dur / 60
    if m < 10:
        return "短视频"
    if m < 40:
        return "中长视频"
    return "长视频"


class _Dumper(yaml.SafeDumper):
    """字符串一律单引号；数字/布尔保持裸值（default_style 会把它们也变成 !!int 标签）。"""


def _repr_str(dumper, data):
    style = "|" if "\n" in data else "'"
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style=style)


_Dumper.add_representer(str, _repr_str)


def dump_note(fm: dict, body: str) -> str:
    head = yaml.dump(fm, Dumper=_Dumper, allow_unicode=True, sort_keys=False,
                     default_flow_style=False, width=200)
    return f"---\n{head}---\n\n{body}"


def body_hash(md: str) -> str:
    body = md.split("---", 2)[-1] if md.startswith("---") else md
    return hashlib.sha256(body.strip().encode("utf-8")).hexdigest()[:16]
