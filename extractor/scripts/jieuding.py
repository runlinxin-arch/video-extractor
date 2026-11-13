"""Stage 3：结构勘定的抽样包与校验。

关键约束（SPEC §6）：
  · units 里**没有时间码**，只有 start_anchor_id —— 时间由代码从 utterance 解析
  · units 数量 ∈ [3,9]，no-speech 例外
  · tiling 精确：无空隙、无重叠、单调、首 0 末 duration
"""
from __future__ import annotations

import json
from pathlib import Path


def fmt_hms(sec: float) -> str:
    s = int(sec)
    return f"{s // 3600:02d}:{(s % 3600) // 60:02d}:{s % 60:02d}"


def _line(u: dict) -> str:
    return f"[{u['id']}|{fmt_hms(u['start'])}] {u['text']}"


def build_pack(utts: list[dict], job: dict, cfg: dict) -> str:
    dur = float(job["duration"])
    head_end = min(cfg["jieuding_head_cap_s"], cfg["jieuding_head_frac"] * dur)
    tail_len = min(cfg["jieuding_tail_cap_s"], cfg["jieuding_tail_frac"] * dur)
    tail_start = dur - tail_len

    head = [u for u in utts if u["start"] < head_end]
    tail = [u for u in utts if u["start"] >= tail_start]
    seen: set[str] = {u["id"] for u in head} | {u["id"] for u in tail}
    mid_lo, mid_hi = head_end, max(tail_start, head_end)
    windows: list[tuple[float, float]] = []
    n_win = 4
    # 中段总抽样占比是 mid_frac，均分到各窗口（否则 4 个窗口会各占 10%，合计 40%）
    win = max(cfg["jieuding_mid_frac"] * dur / n_win, 20.0)
    span = max(mid_hi - mid_lo - win, 0.0)
    for i in range(n_win):
        a = mid_lo + span * (i / max(n_win - 1, 1))
        windows.append((a, min(a + win, mid_hi)))

    out: list[str] = []
    out.append("# 勘定输入包")
    out.append("")
    out.append(f"- 标题：{job.get('title')}")
    out.append(f"- 频道：{job.get('channel')}")
    out.append(f"- 时长：{fmt_hms(dur)}（{dur:.0f}s）")
    out.append(f"- 语言：{job.get('lang')}")
    out.append(f"- 转写来源：{job.get('transcript_source')}")
    if job.get("was_live"):
        out.append("- 注意：这是直播存档（was_live），不是直播中")
    out.append(f"- utterance 总数：{len(utts)}")
    out.append("")
    out.append("## 第一段 · 开头")
    out.append(f"（{fmt_hms(0)} – {fmt_hms(head_end)}）")
    out.append("")
    out.extend(_line(u) for u in head)
    out.append("")
    for i, (a, b) in enumerate(windows, start=1):
        seg = [u for u in utts if a <= u["start"] < b and u["id"] not in seen]
        if not seg:
            continue
        seen.update(u["id"] for u in seg)
        out.append(f"## 中段抽样 {i}")
        out.append(f"（{fmt_hms(a)} – {fmt_hms(b)}）")
        out.append("")
        out.extend(_line(u) for u in seg)
        out.append("")
    out.append("## 最后一段 · 结尾")
    out.append(f"（{fmt_hms(tail_start)} – {fmt_hms(dur)}）")
    out.append("")
    out.extend(_line(u) for u in tail)
    out.append("")
    return "\n".join(out)


def validate(data: dict, utts: list[dict], job: dict, cfg: dict) -> dict:
    """校验模型输出的勘定 JSON，并把 anchor id 解析成时间。"""
    dur = float(job["duration"])
    idmap = {u["id"]: u for u in utts}
    errors: list[str] = []
    warnings: list[str] = []

    ct = (data.get("content_type") or "").strip()
    if ct not in {"lecture", "interview", "narrative", "mixed", "no-speech"}:
        errors.append(f"content_type 非法：{ct!r}")
    sk = (data.get("skeleton") or "").strip()
    if sk not in {"argument", "topic", "arc"}:
        errors.append(f"skeleton 非法：{sk!r}")

    raw_units = data.get("units") or []
    if ct == "no-speech":
        if raw_units:
            warnings.append("content_type=no-speech 但给了 units，已忽略")
        return {
            "content_type": ct, "skeleton": sk, "lang": data.get("lang"),
            "units": [], "glossary": data.get("glossary") or [],
            "errors": errors, "warnings": warnings, "units_derived": False,
        }

    n = len(raw_units)
    if not (cfg["min_units"] <= n <= cfg["max_units"]):
        errors.append(f"G1 违反：units 数量 {n} 不在 [{cfg['min_units']},{cfg['max_units']}]")

    starts: list[float] = []
    labels: list[str] = []
    for i, u in enumerate(raw_units):
        label = (u.get("label") or "").strip()
        if not label:
            errors.append(f"G6 违反：第 {i + 1} 段 label 为空")
        elif len(label) > 12:
            warnings.append(f"第 {i + 1} 段 label 过长（{len(label)} 字），已接受但建议 ≤12")
        aid = u.get("start_anchor_id")
        if aid not in idmap:
            errors.append(f"G5 违反：第 {i + 1} 段 start_anchor_id={aid!r} 不存在")
            starts.append(float("nan"))
        else:
            starts.append(idmap[aid]["start"])
        labels.append(label or f"第{i + 1}段")
        eid = u.get("end_anchor_id")
        if eid is not None and i + 1 < n:
            nxt = raw_units[i + 1].get("start_anchor_id")
            if eid != nxt:
                warnings.append(f"第 {i + 1} 段的 end_anchor_id 与下一段 start 不一致，以后者为准")

    if errors:
        return {"content_type": ct, "skeleton": sk, "lang": data.get("lang"),
                "units": [], "glossary": data.get("glossary") or [],
                "errors": errors, "warnings": warnings, "units_derived": False}

    for i in range(1, len(starts)):
        if not (starts[i] > starts[i - 1]):
            errors.append(f"G3 违反：第 {i + 1} 段起点未严格递增")

    units = []
    for i in range(len(raw_units)):
        a = 0.0 if i == 0 else starts[i]
        b = starts[i + 1] if i + 1 < len(starts) else dur
        if b <= a:
            errors.append(f"第 {i + 1} 段 end<=start（{a:.1f} → {b:.1f}）")
        utype = (raw_units[i].get("type") or "").strip()
        units.append({"i": i + 1, "label": labels[i], "type": utype,
                      "start": round(a, 3), "end": round(b, 3)})

    if units and abs(units[0]["start"]) > 0.001:
        warnings.append("首段起点已强制归零")
        units[0]["start"] = 0.0
    if units:
        units[-1]["end"] = round(dur, 3)

    return {
        "content_type": ct, "skeleton": sk, "lang": data.get("lang"),
        "units": units, "glossary": data.get("glossary") or [],
        "errors": errors, "warnings": warnings, "units_derived": False,
    }


def derive_equal_units(utts: list[dict], dur: float, k: int = 3) -> list[dict]:
    """模型给不出合规分段时的兜底：按时间等分（标 units_derived）。"""
    units = []
    for i in range(k):
        a = dur * i / k
        b = dur * (i + 1) / k
        units.append({"i": i + 1, "label": f"第{i + 1}段", "start": round(a, 3), "end": round(b, 3)})
    return units
