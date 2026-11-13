"""Stage 6：落盘。文件名净化、幂等、默认另存、--force 覆盖（SPEC §9.2 / §14）。"""
from __future__ import annotations

import datetime as _dt
import hashlib
import re
from pathlib import Path

import yaml

INVALID = r'[/\\:*?"<>|]'


def sanitize(title: str, maxlen: int = 80) -> str:
    s = re.sub(INVALID, "-", title)
    s = s.replace("\n", " ").replace("\r", " ")
    s = re.sub(r"\s+", " ", s).strip(" .")
    if len(s) > maxlen:
        cut = s[:maxlen]
        # 尽量在词边界截断，避免出现 "... Dr Sah…" 这种断词
        if " " in cut[maxlen // 2 :]:
            cut = cut[: cut.rfind(" ")]
        words = cut.split()
        while words and len(words[-1].strip(".,;:!?-")) <= 3:
            words.pop()           # 别让文件名以 "Dr…" / "a…" 这种残词结尾
        cut = " ".join(words)
        s = cut.rstrip(" .-，、,:;") + "…"
    return s or "未命名"


def parse_fm(text: str) -> tuple[dict, str]:
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text
    try:
        fm = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError:
        fm = {}
    return fm if isinstance(fm, dict) else {}, parts[2].lstrip("\n")


def body_hash(text: str) -> str:
    _, body = parse_fm(text)
    return hashlib.sha256(body.strip().encode("utf-8")).hexdigest()[:16]


def find_existing(notes_dir: Path, source_id: str) -> list[Path]:
    out = []
    if not notes_dir.exists():
        return out
    for p in notes_dir.glob("*.md"):
        try:
            fm, _ = parse_fm(p.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
        if str(fm.get("source_id", "")).strip() == source_id:
            out.append(p)
    out.sort(key=lambda p: p.stat().st_mtime)
    return out


def _set_fm_scalar(text: str, key: str, value: str) -> str:
    if not text.startswith("---"):
        return text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return text
    head = parts[1]
    # 键可能被 YAML 输出成带引号形式（'首次处理':），必须两种都能匹配，
    # 否则会"匹配不上 → 追加一行"，造成重复键
    pat = re.compile(rf"^('?){re.escape(key)}\1\s*:.*$", re.M)
    if pat.search(head):
        # 沿用原行的键写法（带引号就继续带引号），避免前后风格不一
        head = pat.sub(lambda m: f"{m.group(1)}{key}{m.group(1)}: '{value}'", head)
    else:
        head = head.rstrip("\n") + f"\n'{key}': '{value}'\n"
    return f"---{head}---{parts[2]}"


def save(vault: Path, notes_sub: str, md_text: str, draft_json: dict, title: str,
         source_id: str, force: bool = False) -> dict:
    notes_dir = vault / notes_sub
    notes_dir.mkdir(parents=True, exist_ok=True)
    today = _dt.date.today().isoformat()
    base = sanitize(title)
    new_hash = body_hash(md_text)

    existing = find_existing(notes_dir, source_id)
    if existing and not force:
        old = existing[0]
        old_text = old.read_text(encoding="utf-8", errors="replace")
        if body_hash(old_text) == new_hash:
            updated = _set_fm_scalar(old_text, "最近处理", _now())
            old.write_text(updated, encoding="utf-8")
            return {"action": "idempotent", "path": str(old)}

        # 默认另存（决策）
        stem = f"{today} {base} (重跑)"
        cand = notes_dir / f"{stem}.md"
        n = 2
        while cand.exists():
            cand = notes_dir / f"{stem}{n}.md"
            n += 1
        text = _with_rerun_ref(md_text, old.stem)
        cand.write_text(text, encoding="utf-8")
        _write_draft(notes_dir, cand.stem, draft_json)
        return {"action": "rerun_new", "path": str(cand), "原文件": str(old)}

    if existing and force:
        old = existing[0]
        # SPEC §14.2：覆盖时「首次处理」保留，「最近处理」更新
        prev_fm, _ = parse_fm(old.read_text(encoding="utf-8", errors="replace"))
        text = md_text
        if prev_fm.get("首次处理"):
            text = _set_fm_scalar(text, "首次处理", str(prev_fm["首次处理"]))
        text = _set_fm_scalar(text, "最近处理", _now())
        old.write_text(text, encoding="utf-8")
        _write_draft(notes_dir, old.stem, draft_json)
        return {"action": "overwritten", "path": str(old)}

    cand = notes_dir / f"{today} {base}.md"
    if cand.exists():
        n = 2
        while cand.exists():
            cand = notes_dir / f"{today} {base}-{n}.md"
            n += 1
    cand.write_text(md_text, encoding="utf-8")
    _write_draft(notes_dir, cand.stem, draft_json)
    return {"action": "created", "path": str(cand)}


def _write_draft(notes_dir: Path, stem: str, draft: dict) -> Path:
    d = notes_dir / "底稿"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{stem}.json"
    import json
    p.write_text(json.dumps(draft, ensure_ascii=False, indent=1), encoding="utf-8")
    return p


def _with_rerun_ref(md_text: str, old_stem: str) -> str:
    if not md_text.startswith("---"):
        return md_text
    parts = md_text.split("---", 2)
    head = parts[1].rstrip("\n") + f"\n重跑自: '[[{old_stem}]]'\n"
    return f"---{head}---{parts[2]}"


def _now() -> str:
    return _dt.datetime.now().astimezone().strftime("%Y-%m-%dT%H:%M:%S%z")
