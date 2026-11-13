#!/usr/bin/env python3
"""视频凝练器 CLI。智能体按 stage 依次调用，每个 stage 都有可检查的中间产物。

用法示例：
  extract.py preflight
  extract.py fetch <url>
  extract.py clean      --sid youtube:xxx
  extract.py jieuding-pack --sid ...
  extract.py jieuding-check jieuding.json --sid ...
  extract.py unit-pack 2 --sid ...
  extract.py tiliang-check --sid ...
  extract.py review-pack --sid ...
  extract.py review-apply review.json --sid ...
  extract.py assemble  --sid ...
  extract.py write     --sid ... [--force]
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import assemble as asm
import config
import fetch as fetcher
import jieuding
import subs
import tiliang
import writer

try:
    import asr
except Exception:  # noqa: BLE001
    asr = None


def _jobdir(args) -> Path:
    raw = args.sid
    if raw.startswith("youtube:"):
        pass
    elif raw.startswith("http") or ("youtu" in raw and "/" in raw):
        raw = fetcher.source_id(raw)
    d = config.CACHE_ROOT / "jobs" / raw
    if not d.exists():
        die(f"找不到 job 目录：{d}（先跑 fetch）")
    return d


def _load(p: Path):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def _dump(p: Path, obj) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
    return p


def die(msg: str, code: int = 1):
    print(f"ERROR {msg}", file=sys.stderr)
    raise SystemExit(code)


def _now() -> str:
    return _dt.datetime.now().astimezone().strftime("%Y-%m-%dT%H:%M:%S%z")


# ---------------------------------------------------------------- stages

def cmd_preflight(args):
    cfg = config.load(getattr(args, "config", None))
    r = fetcher.preflight(cfg)
    print(json.dumps(r, ensure_ascii=False, indent=1))
    if not r["ok"]:
        print("\n[stage1.network] 网络不可达。代理可能是按需启用的，请先打开代理。",
              file=sys.stderr)
        raise SystemExit(2)


def cmd_fetch(args):
    cfg = config.load(getattr(args, "config", None))
    html = fetcher.preflight(cfg)
    if not html["ok"]:
        die(f"[stage1.network] 代理/网络不可达：{html['error']}（代理可能未启动）")
    jd = config.job_dir(fetcher.source_id(args.url))
    try:
        job = fetcher.fetch(args.url, cfg, jd)
    except fetcher.FetchError as e:
        print(json.dumps(e.as_dict(), ensure_ascii=False, indent=1), file=sys.stderr)
        raise SystemExit(3)
    print(json.dumps(job, ensure_ascii=False, indent=1))


def cmd_clean(args):
    cfg = config.load(getattr(args, "config", None))
    jd = _jobdir(args)
    job = _load(jd / "job.json")
    lang = job.get("lang")

    if job.get("sub_path"):
        cues = subs.parse_captions(job["sub_path"])
        source = job["transcript_source"]
    elif job.get("audio_path"):
        if asr is None or not asr.available():
            die("mlx-whisper 未安装，无法走 ASR 路径")
        print("… ASR 转写中（首次会下载模型）", file=sys.stderr)
        try:
            cues, lang = asr.transcribe(job["audio_path"], cfg)
        except asr.ASRError as e:
            print(json.dumps(e.as_dict(), ensure_ascii=False, indent=1), file=sys.stderr)
            raise SystemExit(7)
        job["transcript_source"] = "asr"
        job["lang"] = lang          # 检测到的语言必须写回，否则 frontmatter 会是 und
        source = "asr"
        _dump(jd / "job.json", job)
    else:
        die("job 里既没有字幕也没有音频，需要重跑 fetch")

    text, times, endtimes, speakers, stats = subs.assemble(
        cues, cfg, word_tokens=(source == "asr"))
    utts = subs.build_utterances(text, times, endtimes, speakers, lang, cfg)
    subs.assert_contract(utts)
    quality = subs.quality_report(utts, stats, float(job["duration"]), cfg)

    _dump(jd / "utterances.json", utts)
    _dump(jd / "quality.json", quality)
    print(json.dumps({"source": source, **quality}, ensure_ascii=False, indent=1))
    if not quality["ok"]:
        print("\n[stage2.quality] 字幕质量断言未通过。若走的是字幕路径，建议改用 ASR。",
              file=sys.stderr)
        raise SystemExit(4)


def cmd_jieuding_pack(args):
    cfg = config.load(getattr(args, "config", None))
    jd = _jobdir(args)
    job = _load(jd / "job.json")
    utts = _load(jd / "utterances.json")
    pack = jieuding.build_pack(utts, job, cfg)
    p = jd / "jieuding_pack.txt"
    p.write_text(pack, encoding="utf-8")
    print(p)
    print(f"（{len(pack.splitlines())} 行，{len(pack)} 字符）", file=sys.stderr)


def cmd_jieuding_check(args):
    cfg = config.load(getattr(args, "config", None))
    jd = _jobdir(args)
    job = _load(jd / "job.json")
    utts = _load(jd / "utterances.json")
    data = _load(Path(args.file))
    res = jieuding.validate(data, utts, job, cfg)

    if res["errors"]:
        print(json.dumps(res, ensure_ascii=False, indent=1))
        print("\n勘定不合规，按 SPEC §6.5 处理：重试一次；仍失败则等分兜底。", file=sys.stderr)
        raise SystemExit(5)
    if not res["units"] and res["content_type"] != "no-speech":
        res["units"] = jieuding.derive_equal_units(utts, float(job["duration"]))
        res["units_derived"] = True
    _dump(jd / "units.json", res)
    print(json.dumps({k: v for k, v in res.items() if k != "glossary"}, ensure_ascii=False, indent=1))


def cmd_unit_pack(args):
    cfg = config.load(getattr(args, "config", None))
    jd = _jobdir(args)
    utts = _load(jd / "utterances.json")
    units = _load(jd / "units.json")["units"]
    unit = next((u for u in units if u["i"] == args.index), None)
    if not unit:
        die(f"没有第 {args.index} 段（共 {len(units)} 段）")
    pack = tiliang.pack_unit(unit, utts, cfg)
    p = jd / f"unit-{args.index}-pack.txt"
    p.write_text(pack, encoding="utf-8")
    print(p)
    print(f"（{len(tiliang.units_utts(unit, utts))} 条 utterance）", file=sys.stderr)


def cmd_tiliang_check(args):
    jd = _jobdir(args)
    utts = _load(jd / "utterances.json")
    units = _load(jd / "units.json")["units"]
    files = {int(p.stem.split("-")[1]): p for p in jd.glob("claims-*.json")}
    res = tiliang.check_claims(files, units, utts)
    _dump(jd / "claims.json", res["claims"])
    _dump(jd / "claims_stats.json", res["stats"])
    print(json.dumps(res["stats"], ensure_ascii=False, indent=1))
    if res["stats"]["all_failed"]:
        print("\n[stage4.all_ids_invalid] 全部单元提炼失败。按 SPEC §13 产出降级笔记。",
              file=sys.stderr)
        raise SystemExit(6)


def cmd_review_pack(args):
    jd = _jobdir(args)
    utts = _load(jd / "utterances.json")
    claims = _load(jd / "claims.json")
    pack = tiliang.build_review_pack(claims, utts)
    p = _dump(jd / "review_pack.json", pack)
    print(p)
    print(f"（{len(pack['items'])} 条待复核）", file=sys.stderr)


def cmd_review_apply(args):
    jd = _jobdir(args)
    claims = _load(jd / "claims.json")
    results = _load(Path(args.file))
    if isinstance(results, dict):
        results = results.get("results") or results.get("items") or []
    claims, stat = tiliang.apply_review(claims, results)
    _dump(jd / "claims.json", claims)
    print(json.dumps(stat, ensure_ascii=False, indent=1))


def cmd_assemble(args):
    cfg = config.load(getattr(args, "config", None))
    jd = _jobdir(args)
    job = _load(jd / "job.json")
    units = _load(jd / "units.json")
    claims = _load(jd / "claims.json") if (jd / "claims.json").exists() else []
    quality = _load(jd / "quality.json") if (jd / "quality.json").exists() else {}
    card = _load(jd / "card.json") if (jd / "card.json").exists() else {}

    if not claims:
        card.setdefault("degraded", True)
        card.setdefault("degraded_reason", "all_ids_invalid")

    job["_jobdir"] = str(jd)
    job["_date"] = _dt.date.today().isoformat()
    job["_now"] = _now()
    notes_dir = cfg.get("notes_dir", "视频笔记")
    draft_rel = f"{notes_dir}/底稿/{job['_date']} {writer.sanitize(job['title'])}"

    glossary = card.get("glossary") or units.get("glossary") or []
    fm, body = asm.render(job, units.get("units", []), claims, card, cfg, quality,
                          glossary, draft_rel)
    md = asm.dump_note(fm, body)
    p = jd / "note.md"
    p.write_text(md, encoding="utf-8")
    print(p)
    print(f"（{len(body)} 字符，{len(units.get('units', []))} 段，{len(claims)} 条结论）",
          file=sys.stderr)


def cmd_write(args):
    cfg = config.load(getattr(args, "config", None))
    jd = _jobdir(args)
    job = _load(jd / "job.json")
    md = (jd / "note.md").read_text(encoding="utf-8")
    utts = _load(jd / "utterances.json")
    units = _load(jd / "units.json") if (jd / "units.json").exists() else {}
    claims = _load(jd / "claims.json") if (jd / "claims.json").exists() else []

    vault_raw = (cfg.get("vault_path") or "").strip()
    if not vault_raw:
        die("没有配置 Obsidian 库路径。\n"
            "  设置方式二选一：\n"
            "    1) 环境变量  VIDEO_EXTRACTOR_VAULT=/path/to/your/vault\n"
            "    2) 配置文件  ~/.config/video-extractor/config.json  {\"vault_path\": \"/path/to/your/vault\"}")
    vault = Path(vault_raw)
    if not vault.is_dir():
        die(f"库路径不存在：{vault}")
    draft = {
        "source_id": job["source_id"],
        "source_url": job["source_url"],
        "title": job["title"],
        "duration": job["duration"],
        "lang": job.get("lang"),
        "transcript_source": job.get("transcript_source"),
        "units": units.get("units", []),
        "utterances": utts,
        "claims": claims,
        "written_at": _now(),
    }
    res = writer.save(vault, cfg.get("notes_dir", "视频笔记"), md, draft,
                      job["title"], job["source_id"], force=args.force)
    _log(jd, job, res, cfg)
    print(json.dumps(res, ensure_ascii=False, indent=1))


def cmd_corpus_check(args):
    """跑 CORPUS.md 的 A 层自动断言。"""
    import re
    doc = (config.SKILL_ROOT / "CORPUS.md").read_text(encoding="utf-8")
    blocks = re.findall(r"```json\n(.*?)```", doc, re.S)
    if not blocks:
        die("CORPUS.md 里找不到机器可读块")
    corpus = json.loads(blocks[-1])

    rows, fails, pending = [], [], []
    for e in corpus["entries"]:
        name = e["id"]
        if e.get("status") != "frozen" or not e.get("url"):
            pending.append(name)
            rows.append((name, "—", "PENDING", e.get("why", "")))
            continue
        sid = fetcher.source_id(e["url"])
        jd = config.CACHE_ROOT / "jobs" / sid
        if not jd.exists():
            rows.append((name, "—", "SKIP", "还没 fetch 过：先跑 fetch + clean"))
            continue

        jobj = _load(jd / "job.json") if (jd / "job.json").exists() else {}
        q = _load(jd / "quality.json") if (jd / "quality.json").exists() else {}
        utts = _load(jd / "utterances.json") if (jd / "utterances.json").exists() else []
        exp = e.get("expect") or {}
        problems = []

        want_src = exp.get("transcript_source")
        if want_src and jobj.get("transcript_source") != want_src:
            problems.append(f"转录来源是 {jobj.get('transcript_source')!r}，期望 {want_src!r}")
        want_lang = exp.get("lang")
        if want_lang and not str(jobj.get("lang", "")).startswith(want_lang):
            problems.append(f"语言是 {jobj.get('lang')!r}，期望以 {want_lang!r} 开头")
        if not q.get("ok"):
            problems.append(f"质量断言未过 {q.get('problems')}")
        lo, hi = (exp.get("utterances") or [0, 10**9])
        if not (lo <= len(utts) <= hi):
            problems.append(f"utterance 数 {len(utts)} 不在 [{lo},{hi}]")
        cov = q.get("coverage")
        if cov is not None and cov < exp.get("coverage_min", 0):
            problems.append(f"覆盖率 {cov} < {exp.get('coverage_min')}")
        cpm = q.get("chars_per_min")
        cmin, cmax = (exp.get("chars_per_min") or [0, 10**9])
        if cpm is not None and not (cmin <= cpm <= cmax):
            problems.append(f"chars/min {cpm} 不在 [{cmin},{cmax}]")

        note_exp = exp.get("note")
        note = jd / "note.md"
        if note_exp:
            if not note.exists():
                problems.append("缺 note.md（还没 assemble）")
            else:
                md = note.read_text(encoding="utf-8")
                body = md.split("---", 2)[-1]
                order = [body.find(f"## {n}") for n in note_exp.get("sections", [])]
                if any(i < 0 for i in order):
                    problems.append(f"缺章节 {[n for n, i in zip(note_exp['sections'], order) if i < 0]}")
                elif order != sorted(order):
                    problems.append("章节顺序不对")
                for bad in (note_exp.get("forbidden_sections") or []):
                    if f"## {bad}" in body:
                        problems.append(f"不该出现章节「{bad}」")
                cap = note_exp.get("max_body_chars")
                if cap and len(body) > cap:
                    problems.append(f"正文 {len(body)} 字符 > 上限 {cap}")

                # 压缩比分段判据（PRD §7.3）：≥40 分钟看比值，<40 分钟看绝对字数
                zh_note = len(re.findall(r"[\u4e00-\u9fff]", body))
                src_chars = sum(len(u["text"]) for u in utts)
                dur_min = (jobj.get("duration") or 0) / 60.0
                if dur_min >= 40:
                    ratio = zh_note / max(src_chars, 1)
                    if ratio > 0.35:
                        problems.append(f"压缩比 {ratio:.2f} > 0.35（{zh_note}/{src_chars} 字）")
                else:
                    if zh_note > 3000:
                        problems.append(f"短内容笔记 {zh_note} 字 > 3000 上限")
                if "| 时间段 | 主题 | 类型 |" in body:
                    problems.append("仍渲染了结构地图表格")
                if "&list=" in body or "&index=" in body:
                    problems.append("锚点 URL 含 list/index 参数")
                if re.search(r"值得一看|内容充实", body):
                    problems.append("出现判决词")
                units_f = jd / "units.json"
                lo_u, hi_u = (note_exp.get("units") or [0, 10**9])
                if units_f.exists():
                    n_u = len(_load(units_f).get("units", []))
                    if not (lo_u <= n_u <= hi_u):
                        problems.append(f"units {n_u} 不在 [{lo_u},{hi_u}]")
                claims_f = jd / "claims.json"
                if claims_f.exists():
                    cl = _load(claims_f)
                    lo_c, hi_c = (note_exp.get("claims") or [0, 10**9])
                    if not (lo_c <= len(cl) <= hi_c):
                        problems.append(f"claims {len(cl)} 不在 [{lo_c},{hi_c}]")
                    low = sum(1 for c in cl if c.get("low_confidence"))
                    if low and body.count("[需核实]") == 0:
                        problems.append(f"{low} 条低置信但没有 [需核实] 标记")
                    idmap = {u["id"]: u for u in utts}
                    miss = [c["claim_id"] for c in cl
                            if c["evidence_ids"] and f"?t={int(idmap[c['evidence_ids'][0]]['start'])}" not in body]
                    if miss:
                        problems.append(f"{len(miss)} 条结论的锚点未出现在正文")

        if problems:
            fails.append((name, problems))
            rows.append((name, str(len(utts)), "FAIL", "; ".join(problems)))
        else:
            rows.append((name, str(len(utts)), "PASS", ""))

    w = max(len(r[0]) for r in rows) + 2
    print(f"{'样本'.ljust(w)}{'utts':>6}  {'结果':<8}说明")
    print("-" * 78)
    for name, n, st, msg in rows:
        print(f"{name.ljust(w)}{n:>6}  {st:<8}{msg}")
    print("-" * 78)
    print(f"PASS {sum(1 for r in rows if r[2] == 'PASS')}｜"
          f"FAIL {len(fails)}｜PENDING {len(pending)}（缺样本，见 CORPUS.md 表格）")
    if fails:
        raise SystemExit(1)


def cmd_status(args):
    jd = _jobdir(args)
    rows = []
    for p in sorted(jd.rglob("*")):
        if p.is_file():
            rows.append(f"{p.relative_to(jd)}\t{p.stat().st_size}")
    print(f"job: {jd}")
    print("\n".join(rows))


def _log(jd: Path, job: dict, res: dict, cfg: dict):
    d = config.runs_dir()
    payload = {
        "at": _now(),
        "source_id": job["source_id"],
        "status": res.get("action"),
        "note_path": res.get("path"),
        "duration": job["duration"],
        "transcript_source": job.get("transcript_source"),
        "proxy": cfg.get("proxy"),
        "pipeline": cfg.get("pipeline_version", "0.2.0"),
    }
    q = jd / "quality.json"
    if q.exists():
        payload["quality"] = _load(q)
    s = jd / "claims_stats.json"
    if s.exists():
        payload["claims"] = _load(s)
    (d / f"{_dt.datetime.now().strftime('%Y%m%dT%H%M%S')}-{job['source_id'].replace(':','_')}.json"
     ).write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")


# ---------------------------------------------------------------- parser

def main():
    ap = argparse.ArgumentParser(prog="extract.py", description="视频凝练器")
    ap.add_argument("--config", help="配置文件路径")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add(name, fn, help_):
        p = sub.add_parser(name, help=help_)
        p.set_defaults(func=fn)
        return p

    p = add("preflight", cmd_preflight, "探测代理与 YouTube 可达性")
    p.add_argument("--config", help=argparse.SUPPRESS)

    p = add("fetch", cmd_fetch, "取元数据 + 字幕（或音频）")
    p.add_argument("url")

    for name, fn, h in [
        ("clean", cmd_clean, "字幕/ASR → utterances.json"),
        ("jieuding-pack", cmd_jieuding_pack, "生成勘定输入包"),
        ("tiliang-check", cmd_tiliang_check, "校验分块提炼结果"),
        ("review-pack", cmd_review_pack, "生成语义复核包"),
        ("assemble", cmd_assemble, "组装 markdown 草稿"),
        ("write", cmd_write, "落盘到 Obsidian"),
        ("status", cmd_status, "查看 job 目录"),
    ]:
        p = add(name, fn, h)
        p.add_argument("--sid", required=True, help="job 标识或 url")
        if name == "write":
            p.add_argument("--force", action="store_true", help="覆盖既有笔记")

    p = add("jieuding-check", cmd_jieuding_check, "校验勘定 JSON 并解析边界")
    p.add_argument("file")
    p.add_argument("--sid", required=True)

    p = add("unit-pack", cmd_unit_pack, "生成某段的提炼输入")
    p.add_argument("index", type=int)
    p.add_argument("--sid", required=True)

    p = add("review-apply", cmd_review_apply, "应用复核结果")
    p.add_argument("file")
    p.add_argument("--sid", required=True)

    add("corpus-check", cmd_corpus_check, "跑验收语料（CORPUS.md）的自动断言")

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
