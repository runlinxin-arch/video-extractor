"""Stage 4 + 4.5：分块提炼的输入打包、id 校验、结论语义复核队列。"""
from __future__ import annotations

import json
from pathlib import Path

CLAIM_TYPES = {"论点", "事件", "转折", "闲聊"}


def units_utts(unit: dict, utts: list[dict]) -> list[dict]:
    return [u for u in utts if unit["start"] <= u["start"] < unit["end"]]


def pack_unit(unit: dict, utts: list[dict], cfg: dict) -> str:
    seg = units_utts(unit, utts)
    soft_s = cfg["unit_soft_limit_s"]
    soft_c = cfg["unit_soft_limit_chars"]

    # unit 内二次切分（v0.1 缺失）：unit 本身过大时，分块设计的假设就不成立了
    chunks: list[list[dict]] = []
    cur: list[dict] = []
    cur_chars = 0
    cur_start = None
    for u in seg:
        if cur_start is None:
            cur_start = u["start"]
        over_s = (u["end"] - cur_start) > soft_s
        over_c = (cur_chars + len(u["text"])) > soft_c
        if cur and (over_s or over_c):
            chunks.append(cur)
            cur, cur_chars, cur_start = [], 0, u["start"]
        cur.append(u)
        cur_chars += len(u["text"])
    if cur:
        chunks.append(cur)

    out = [
        f"# 单元 {unit['i']} · {unit['label']}",
        "",
        f"区间：{unit['start']:.1f}s – {unit['end']:.1f}s（{unit['end'] - unit['start']:.0f}s）",
        f"utterance：{len(seg)} 条｜字符：{sum(len(u['text']) for u in seg)}｜窗口：{len(chunks)}",
        "",
    ]
    for ci, chunk in enumerate(chunks, start=1):
        if len(chunks) > 1:
            out.append(f"## 窗口 {ci}/{len(chunks)}")
            out.append("")
        out.extend(f"[{u['id']}] {u['text']}" for u in chunk)
        out.append("")
    return "\n".join(out)


def check_claims(files: dict[int, Path], units: list[dict], utts: list[dict]) -> dict:
    """校验每个 unit 的提炼结果。id 不存在 → 逐条剔除；全空的 unit 记失败。"""
    valid_ids: dict[int, set[str]] = {}
    for unit in units:
        valid_ids[unit["i"]] = {u["id"] for u in units_utts(unit, utts)}

    claims: list[dict] = []
    stats = {"total_raw": 0, "kept": 0, "dropped_claims": 0, "invalid_ids": 0,
             "units_with_claims": 0, "units_failed": [], "all_failed": False,
             "details": []}
    idx = 0

    for unit in units:
        p = files.get(unit["i"])
        if not p or not Path(p).exists():
            stats["units_failed"].append(unit["i"])
            continue
        try:
            raw = json.loads(Path(p).read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            stats["units_failed"].append(unit["i"])
            continue
        if isinstance(raw, dict):
            raw = raw.get("claims") or []
        kept_here = 0
        for item in raw:
            stats["total_raw"] += 1
            ids = [str(x) for x in (item.get("evidence_ids") or [])]
            good = [x for x in ids if x in valid_ids[unit["i"]]]
            bad = [x for x in ids if x not in valid_ids[unit["i"]]]
            if bad:
                stats["invalid_ids"] += len(bad)
                stats["details"].append({"unit": unit["i"], "claim": (item.get("claim") or "")[:40],
                                         "invalid": bad})
            text = (item.get("claim") or "").strip()
            ctype = (item.get("type") or "").strip()
            if not text or not good:
                stats["dropped_claims"] += 1
                continue
            if ctype not in CLAIM_TYPES:
                ctype = "论点"
            idx += 1
            zh = item.get("evidence_zh") or []
            claims.append({
                "claim_id": f"c{idx:03d}",
                "unit": unit["i"],
                "claim": text,
                "evidence_ids": good,
                "evidence_zh": [str(x) for x in zh][: len(good)] if isinstance(zh, list) else [],
                "counter": (item.get("counter") or None),
                "type": ctype,
                "low_confidence": False,
            })
            kept_here += 1
        if kept_here:
            stats["units_with_claims"] += 1
        else:
            stats["units_failed"].append(unit["i"])

    stats["kept"] = len(claims)
    stats["all_failed"] = len(claims) == 0
    return {"claims": claims, "stats": stats}


def build_review_pack(claims: list[dict], utts: list[dict]) -> dict:
    """每条「论点」抽 1 个 evidence 做语义复核。"""
    idmap = {u["id"]: u for u in utts}
    items = []
    for c in claims:
        if c["type"] != "论点":
            continue
        eid = c["evidence_ids"][0]
        u = idmap.get(eid)
        if not u:
            continue
        items.append({
            "claim_id": c["claim_id"],
            "claim": c["claim"],
            "evidence_id": eid,
            "evidence_text": u["text"],
        })
    return {"instruction": "判断 evidence_text 是否支持 claim。只输出 JSON 数组："
                           "[{\"claim_id\": \"c001\", \"supported\": true|false}]",
            "items": items}


def apply_review(claims: list[dict], results: list[dict]) -> tuple[list[dict], dict]:
    verdict = {r.get("claim_id"): bool(r.get("supported")) for r in results}
    stat = {"reviewed": 0, "unsupported": 0}
    for c in claims:
        if c["claim_id"] in verdict:
            stat["reviewed"] += 1
            if not verdict[c["claim_id"]]:
                c["low_confidence"] = True
                stat["unsupported"] += 1
    return claims, stat


def pick_top3(claims: list[dict], max_n: int = 3) -> list[dict]:
    """三条最硬结论：规则，不是模型感觉（SPEC §9.5）。"""
    pool = [c for c in claims if not c["low_confidence"]]
    strong = [c for c in pool if len(c["evidence_ids"]) >= 2]
    cand = strong or pool
    if not cand:
        return []
    rank = {"论点": 3, "转折": 2, "事件": 1, "闲聊": 0}
    ranked = sorted(cand, key=lambda c: (rank.get(c["type"], 0),
                                         len(c["evidence_ids"]), len(c["claim"])), reverse=True)
    return ranked[:max_n]
