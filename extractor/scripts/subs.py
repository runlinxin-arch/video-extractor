"""字幕解析与清洗 → utterance 契约。

设计依据见 SPEC §4/§5。关键结论（实测得出，与 v0.1 规格不同）：
  · 不能按"时长 ≤50ms 就丢"过滤 —— YouTube 自动字幕里 10ms 的 cue 常常承载**新词**，
    丢掉会丢真实内容。正确做法是重叠合并（overlap merge）。
  · 自动字幕是滚动窗口：相邻 cue 之间是"尾部重叠"，不是简单的"完全相同"，
    因此用最长后缀-前缀重叠来拼接，而不是相等判断。
"""
from __future__ import annotations

import html
import re
from dataclasses import dataclass
from typing import Iterable

TAG_RE = re.compile(r"<[^>]*>")
TIMING_RE = re.compile(
    r"(?:(\d{1,3}):)?(\d{1,2}):(\d{2})(?:[.,](\d{1,3}))?\s*-->\s*"
    r"(?:(\d{1,3}):)?(\d{1,2}):(\d{2})(?:[.,](\d{1,3}))?"
)
SENT_END = "。！？!?…"
COMMA = "，,、；;·"

CJK_RE = re.compile(r"[\u3400-\u9fff\uf900-\ufaff]")


@dataclass
class Cue:
    start: float
    end: float
    raw: str
    lead: bool = False        # ASR 词级 token：是否自带前导空格


def _to_sec(h: str | None, m: str, s: str, ms: str | None) -> float:
    return (
        (int(h) if h else 0) * 3600
        + int(m) * 60
        + int(s)
        + (int((ms or "0").ljust(3, "0")) / 1000.0)
    )


def parse_captions(path: str) -> list[Cue]:
    """解析 VTT / SRT。对 yt-dlp 产出的两种怪格式都容错。"""
    text = open(path, encoding="utf-8", errors="replace").read()
    blocks = re.split(r"\r?\n\s*\r?\n", text)
    cues: list[Cue] = []
    for block in blocks:
        lines = [ln for ln in block.split("\n")]
        idx = next((i for i, ln in enumerate(lines) if "-->" in ln), None)
        if idx is None:
            continue
        m = TIMING_RE.search(lines[idx])
        if not m:
            continue
        g = m.groups()
        start = _to_sec(g[0], g[1], g[2], g[3])
        end = _to_sec(g[4], g[5], g[6], g[7])
        body = "\n".join(lines[idx + 1 :])
        cues.append(Cue(start, end, body))
    cues.sort(key=lambda c: (c.start, c.end))
    return cues


def strip_markup(s: str) -> tuple[str, int]:
    """剥标签/实体，返回 (纯文本, 说话人变更标记数)。"""
    s = TAG_RE.sub("", s)
    s = html.unescape(s)
    n_speaker = s.count(">>")
    s = s.replace(">>", " ")
    s = s.replace("\u00a0", " ")
    s = re.sub(r"\]\s*\[", "] [", s)           # [music][music] → [music] [music]
    s = re.sub(r"\[(?:music|applause|laughter)\]", "", s, flags=re.I)  # 纯噪声标记
    s = re.sub(r"\](?=[A-Za-z\u3400-\u9fff])", "] ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s, n_speaker


def _is_cjk(ch: str) -> bool:
    return bool(CJK_RE.match(ch))


def assemble(cues: Iterable[Cue], cfg: dict,
             word_tokens: bool = False) -> tuple[str, list[float], list[float], list[int], dict]:
    """重叠合并成一条带时间轴的字符流。

    返回 (text, per_char_start, per_char_cue_end, per_char_speaker, stats)
    每字符记录"所属 cue 的起点"与"所属 cue 的终点"，切句时用来定 start/end。
    """
    min_overlap = int(cfg["min_overlap_chars"])
    chars: list[str] = []
    times: list[float] = []
    endtimes: list[float] = []
    speakers: list[int] = []
    speaker_idx = 0

    raw_cue_count = 0
    blank_cues = 0
    tiny_cues = 0
    raw_chars = 0
    tail_len = 240

    for cue in cues:
        raw_cue_count += 1
        dur = cue.end - cue.start
        if dur <= 0.05:
            tiny_cues += 1
        text, n_spk = strip_markup(cue.raw)
        if n_spk:
            speaker_idx += n_spk
        if not text:
            blank_cues += 1
            continue
        raw_chars += len(text)

        tail = "".join(chars[-tail_len:])
        maxk = min(len(tail), len(text))
        k = 0
        if maxk >= min_overlap:
            for cand in range(maxk, min_overlap - 1, -1):
                if tail.endswith(text[:cand]):
                    k = cand
                    break
        add = text[k:]
        if not add.strip():
            continue

        span = max(cue.end - cue.start, 0.001)
        n = len(text)
        # ASR 词级 token：以 token 自带的前导空格为准（whisper 会把 "Vlog" 拆成
        # "V" + "log" 两个 token，补空格就会变成 "V log"）
        if chars and cue.lead and not chars[-1].isspace():
            chars.append(" ")
            times.append(times[-1] if times else cue.start)
            endtimes.append(endtimes[-1] if endtimes else cue.end)
            speakers.append(speaker_idx)

        # 跨 cue 拼接时补分隔：拉丁文之间补空格，标点/中日韩不补
        if not word_tokens and chars and add and not add[0].isspace() and not chars[-1].isspace():
            a0 = add.lstrip()[0]
            left_cjk, right_cjk = _is_cjk(chars[-1]), _is_cjk(a0)
            if not (left_cjk or right_cjk) and a0 not in ",.;:!?)]}\u201d\u2019":
                chars.append(" ")
                times.append(times[-1] if times else cue.start)
                endtimes.append(endtimes[-1] if endtimes else cue.end)
                speakers.append(speaker_idx)
        for j in range(k, n):
            ch = text[j]
            t = cue.start + ((j + 1) / n) * span
            if times and t < times[-1]:
                t = times[-1]
            chars.append(ch)
            times.append(t)
            endtimes.append(cue.end)
            speakers.append(speaker_idx)

    # 去掉首尾空白（同步时间轴）
    while chars and not chars[0].strip():
        chars.pop(0); times.pop(0); endtimes.pop(0); speakers.pop(0)
    while chars and not chars[-1].strip():
        chars.pop(); times.pop(); endtimes.pop(); speakers.pop()

    stats = {
        "raw_cue_count": raw_cue_count,
        "blank_cues": blank_cues,
        "tiny_cues": tiny_cues,
        "raw_chars": raw_chars,
        "assembled_chars": len(chars),
        "dedup_ratio": (raw_chars / len(chars)) if chars else 0.0,
        "span": (times[-1] - times[0]) if times else 0.0,
    }
    return "".join(chars), times, endtimes, speakers, stats


def _split_long(text: str, start_i: int, limit_chars: int, limit_words: int | None) -> list[int]:
    """返回切分位置（相对 text）。"""
    cuts: list[int] = []
    i = 0
    n = len(text)
    while i < n:
        if limit_words:
            # 按词数切：向前找第 limit_words 个词之后最近的空白/标点
            words = 0
            j = i
            while j < n:
                if text[j].isspace():
                    words += 1
                    if words >= limit_words:
                        break
                j += 1
            limit = j
        else:
            limit = i + limit_chars
        if limit >= n:
            cuts.append(n)
            break
        # 优先在逗号处切
        best = -1
        for k in range(limit, i, -1):
            if k - i < 8:
                break
            if text[k - 1] in COMMA:
                best = k
                break
        if best < 0:
            for k in range(limit, i, -1):
                if k - i < 8:
                    break
                if text[k - 1].isspace():
                    best = k
                    break
        if best < 0:
            # 中文没有空格，兜底是硬切；但不能把 "Vlog" 劈成 "V"/"log"
            cut = min(limit, n)
            while cut > i + 8 and cut < n and \
                    text[cut - 1].isascii() and text[cut - 1].isalnum() and \
                    text[cut].isascii() and text[cut].isalnum():
                cut -= 1
            best = cut
        cuts.append(best if best > 0 else limit)
        i = cuts[-1]
    return cuts


def build_utterances(
    assembled: str,
    times: list[float],
    endtimes: list[float],
    speakers: list[int],
    lang: str,
    cfg: dict,
) -> list[dict]:
    """切句 → utterance 契约。"""
    if not assembled:
        return []

    gap = float(cfg["sentence_gap_s"])
    is_zh = (lang or "").lower().startswith("zh")
    max_chars = int(cfg["utterance_max_zh"]) if is_zh else 0
    max_words = None if is_zh else int(cfg["utterance_max_en"])

    # 1) 候选边界：句末标点 + 静默间隔
    bounds: set[int] = set()
    for i, ch in enumerate(assembled):
        if ch in SENT_END:
            bounds.add(i + 1)
        elif ch == ".":
            nxt = assembled[i + 1] if i + 1 < len(assembled) else " "
            prev = assembled[i - 1] if i > 0 else " "
            if nxt.isspace() and not prev.isdigit():
                j = i + 1
                while j < len(assembled) and assembled[j].isspace():
                    j += 1
                if j >= len(assembled) or assembled[j].isupper() or assembled[j] in "[(" or _is_cjk(assembled[j]):
                    bounds.add(i + 1)
    for i in range(1, len(times)):
        if times[i] - times[i - 1] > gap:
            bounds.add(i)
    bounds.add(len(assembled))

    # 边界不能落在 ASCII 单词内部：静默切分点常常正好在 "V|log" 这种位置，
    # 而中文没有空格、也没有标点，看不出被劈开了。把边界推到词尾。
    def _align(b: int) -> int:
        while 0 < b < len(assembled) and \
                assembled[b - 1].isascii() and assembled[b - 1].isalnum() and \
                assembled[b].isascii() and assembled[b].isalnum():
            b += 1
        return b

    ordered = sorted({_align(b) for b in bounds if b > 0})
    spans: list[tuple[int, int]] = []
    prev = 0
    for b in ordered:
        if b <= prev:
            continue
        spans.append((prev, b))
        prev = b
    if prev < len(assembled):
        spans.append((prev, len(assembled)))

    # 2) 切分过长的 span
    final_spans: list[tuple[int, int]] = []
    for a, b in spans:
        seg = assembled[a:b]
        stripped = seg.strip()
        if not stripped:
            continue
        offset = len(seg) - len(seg.lstrip())
        a2 = a + offset
        seg = assembled[a2:b].rstrip()
        if not seg:
            continue
        cuts = _split_long(seg, 0, max_chars or 10**9, max_words)
        cur = 0
        for c in cuts:
            part = seg[cur:c]
            if part.strip():
                final_spans.append((a2 + cur, a2 + c))
            cur = c

    # 2.4) 时长兜底：ASR 常常没有句末标点，一条能拖到十几秒 → 按口语时长硬切
    limit_s = float(cfg.get("utterance_max_seconds") or 0)
    if limit_s > 0:
        out_spans: list[tuple[int, int]] = []
        for a, b in final_spans:
            while times[min(b, len(times)) - 1] - times[a] > limit_s:
                target = times[a] + limit_s
                cut = next((i for i in range(a + 1, b) if times[i] >= target), b)
                # 回退到最近的空格，避免把单词切断
                for i in range(cut, max(a + 8, a + 1), -1):
                    if assembled[i - 1].isspace():
                        cut = i
                        break
                if cut >= b or cut <= a:
                    break
                out_spans.append((a, cut))
                a = cut
            out_spans.append((a, b))
        final_spans = out_spans

    # 2.5) 后处理：右括号/引号/连珠标点并入；过短碎片并入前一条
    CLOSERS = ")]}\"'\u201d\u2019.\u2026!?\uff01\uff1f"
    spans2: list[list[int]] = []
    for a, b in final_spans:
        while b < len(assembled) and assembled[b] in CLOSERS:
            b += 1
        if spans2 and a < spans2[-1][1]:
            spans2[-1][1] = max(spans2[-1][1], b)
        else:
            spans2.append([a, b])

    def _wordy(t: str) -> int:
        return sum(1 for ch in t if ch.isalnum() or _is_cjk(ch))

    spans3: list[list[int]] = []
    for a, b in spans2:
        if spans3 and _wordy(assembled[a:b]) < 2:
            spans3[-1][1] = b
        else:
            spans3.append([a, b])
    final_spans = [(a, b) for a, b in spans3]

    # 3) 产出
    out: list[dict] = []
    prev_end = -1.0
    for n, (a, b) in enumerate(final_spans, start=1):
        text = assembled[a:b].strip()
        if not text:
            continue
        start = max(times[a], prev_end)
        end = max(endtimes[min(b, len(endtimes)) - 1], start + 0.05)
        prev_end = end
        spk = speakers[a]
        out.append(
            {
                "id": f"u{n:04d}",
                "start": round(start, 3),
                "end": round(end, 3),
                "text": text,
                "lang": lang or "und",
                "speaker": spk if spk > 0 else None,
            }
        )
    # 收尾：把 <2s 的微空隙并给前一句（不影响锚点，锚点只用 start）
    for i in range(len(out) - 1):
        if out[i + 1]["start"] - out[i]["end"] < 2.0:
            out[i]["end"] = out[i + 1]["start"]
    if out:
        out[-1]["end"] = max(out[-1]["end"], times[-1])
    return out


def assert_contract(utts: list[dict]) -> None:
    """U1–U5 断言。"""
    seen: set[str] = set()
    prev_start = -1.0
    prev_end = -1.0
    for u in utts:
        if u["id"] in seen:
            raise ValueError(f"U1 违反：id 重复 {u['id']}")
        seen.add(u["id"])
        if u["start"] < prev_start - 1e-6:
            raise ValueError(f"U2 违反：start 非单调（{u['id']}）")
        if u["end"] <= u["start"]:
            raise ValueError(f"U3 违反：end<=start（{u['id']}）")
        if u["start"] < prev_end - 1e-6:
            raise ValueError(f"U4 违反：与上一条重叠（{u['id']}）")
        if not u["text"].strip():
            raise ValueError(f"U5 违反：空文本（{u['id']}）")
        prev_start, prev_end = u["start"], u["end"]


def quality_report(utts: list[dict], stats: dict, duration: float, cfg: dict) -> dict:
    minutes = max(duration / 60.0, 1e-6)
    chars = sum(len(u["text"]) for u in utts)
    span = stats.get("span") or (utts[-1]["end"] - utts[0]["start"] if utts else 0.0)
    # 句末标点密度：中文无标点字幕会让断句退化成"按字数硬切"
    punct = sum(1 for u in utts if u["text"].rstrip()[-1:] and u["text"].rstrip()[-1] in "。！？!?…")
    no_punct = (punct / len(utts)) < 0.05 if utts else False

    rep = {
        "utterance_count": len(utts),
        "sentence_end_ratio": round(punct / len(utts), 3) if utts else 0.0,
        "no_punctuation": no_punct,
        "chars": chars,
        "chars_per_min": round(chars / minutes, 1),
        "coverage": round(span / duration, 3) if duration else 0.0,
        "dedup_ratio": round(stats.get("dedup_ratio", 0.0), 2),
        "raw_cues": stats.get("raw_cue_count", 0),
        "blank_cues": stats.get("blank_cues", 0),
        "tiny_cues": stats.get("tiny_cues", 0),
        "dropped_cue_ratio": round(
            (stats.get("blank_cues", 0) / max(stats.get("raw_cue_count", 1), 1)), 3
        ),
    }
    problems: list[str] = []
    if rep["coverage"] < cfg["min_coverage"]:
        problems.append(
            f"coverage {rep['coverage']} < {cfg['min_coverage']}（字幕只剩部分时长）"
        )
    if not (cfg["chars_per_min_min"] <= rep["chars_per_min"] <= cfg["chars_per_min_max"]):
        problems.append(
            f"chars_per_min {rep['chars_per_min']} 越界 "
            f"[{cfg['chars_per_min_min']}, {cfg['chars_per_min_max']}]"
        )
    if rep["dedup_ratio"] > cfg["max_dedup_ratio"]:
        problems.append(f"dedup_ratio {rep['dedup_ratio']} > {cfg['max_dedup_ratio']}")
    rep["problems"] = problems
    rep["ok"] = not problems
    return rep
