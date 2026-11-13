"""Stage 0–1：归一化输入 + 获取（字幕优先，ASR 兜底）。"""
from __future__ import annotations

import glob
import json
import re
import sys
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

VIDEO_ID_RE = re.compile(
    r"(?:[?&]v=|youtu\.be/|/shorts/|/embed/|/live/|/v/)([A-Za-z0-9_-]{11})"
)
LIST_URL_RE = re.compile(r"[?&](list|index)=")


class FetchError(RuntimeError):
    def __init__(self, stage: str, code: str, message: str, cause: str = "", hint: str = ""):
        self.stage, self.code, self.message = stage, code, message
        self.cause, self.hint = cause, hint
        super().__init__(f"[{stage}] {code}: {message}")

    def as_dict(self) -> dict:
        return {"stage": self.stage, "code": self.code, "message": self.message,
                "cause": self.cause, "hint": self.hint}


def video_id(url: str) -> str:
    m = VIDEO_ID_RE.search(url)
    if not m:
        raise FetchError("stage0", "invalid_url", f"无法从 URL 解析出视频 id：{url}")
    return m.group(1)


def source_id(url: str) -> str:
    return f"youtube:{video_id(url)}"


def _proxy_args(cfg: dict) -> list[str]:
    p = cfg.get("proxy")
    return ["--proxy", p] if p else []


def preflight(cfg: dict) -> dict:
    """前置探活。必须有限超时——代理是按需启用的。"""
    proxy = cfg.get("proxy")
    timeout = max(cfg.get("proxy_timeout_ms", 3000) / 1000.0, 0.5)
    handlers = []
    if proxy:
        handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    opener = urllib.request.build_opener(*handlers)
    req = urllib.request.Request(
        "https://www.youtube.com/", headers={"User-Agent": "Mozilla/5.0 (video-extractor)"}
    )
    t0 = time.time()
    try:
        with opener.open(req, timeout=timeout) as resp:
            ok = resp.status < 500
        return {"ok": ok, "elapsed_s": round(time.time() - t0, 2), "proxy": proxy,
                "status": resp.status, "error": None}
    except urllib.error.HTTPError as e:
        return {"ok": e.code < 500, "elapsed_s": round(time.time() - t0, 2), "proxy": proxy,
                "status": e.code, "error": f"HTTP {e.code}"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "elapsed_s": round(time.time() - t0, 2), "proxy": proxy,
                "status": None, "error": f"{type(e).__name__}: {e}"}


def _ytdlp(cfg: dict, args: list[str], timeout: int = 300) -> subprocess.CompletedProcess:
    cmd = [cfg.get("ytdlp", "yt-dlp"), *_proxy_args(cfg),
           "--socket-timeout", str(cfg.get("socket_timeout_s", 20)),
           "--retries", str(cfg.get("retries", 2)),
           "--no-warnings", "--no-playlist",
           "--remote-components", cfg.get("remote_components", "ejs:github"),
           *args]
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise FetchError("stage1", "timeout", f"yt-dlp 超时 {timeout}s",
                         hint="检查代理/网络；或加大 socket_timeout") from None
    except FileNotFoundError:
        raise FetchError("stage1", "no_ytdlp", "找不到 yt-dlp", hint="pip install -U yt-dlp") from None


def _info(url: str, cfg: dict) -> dict:
    p = _ytdlp(cfg, ["-J", "--skip-download", url], timeout=180)
    if p.returncode != 0:
        err = (p.stderr or "").strip().splitlines()
        last = err[-1] if err else "unknown"
        low = last.lower()
        if "private" in low:
            raise FetchError("stage1", "private_video", "视频是私有的", cause=last)
        if "age" in low and "restrict" in low:
            raise FetchError("stage1", "age_restricted", "视频有年龄限制", cause=last)
        if "not available in your country" in low or "geo" in low:
            raise FetchError("stage1", "geo_blocked", "地区限制", cause=last)
        if "members" in low or "join this channel" in low:
            raise FetchError("stage1", "members_only", "会员专属视频", cause=last)
        if "sign in" in low or "bot" in low:
            raise FetchError("stage1", "auth_required", "需要登录/被风控", cause=last,
                             hint="可配置 cookie 或更换代理出口")
        raise FetchError("stage1", "info_failed", "取元数据失败", cause=last)
    try:
        return json.loads(p.stdout)
    except json.JSONDecodeError:
        raise FetchError("stage1", "info_failed", "yt-dlp 输出不是合法 JSON") from None


TRANSLATION_TAIL_RE = re.compile(r"^[a-z]{2,3}(?:-[A-Za-z]{2,4})?-[a-z]{2}$")


def is_translation_variant(key: str) -> bool:
    """`zh-Hans-en` / `af-en` 这类是"从某语言机翻"的变体，不是原语言字幕。"""
    return bool(TRANSLATION_TAIL_RE.match(key))


def _lang_variants(orig: str, keys) -> list[str]:
    out: list[str] = []
    if orig:
        out.append(orig)
        base = orig.split("-")[0]
        if base == "zh":
            out += ["zh-CN", "zh-Hans", "zh-TW", "zh-Hant"]
        elif base == "en":
            out += ["en", "en-US", "en-GB"]
        out += [k for k in keys if k.split("-")[0] == base]
    seen, uniq = set(), []
    for x in out:
        if x not in seen:
            seen.add(x)
            uniq.append(x)
    return uniq


def pick_language(info: dict, cfg: dict) -> tuple[str | None, str | None, bool]:
    """选字幕语言。**原语言优先**——否则英文视频会选中 `zh-CN` 这类翻译稿（已实测）。

    返回 (lang, source, is_original_language)
    """
    orig = (info.get("language") or "").lower()
    subs = info.get("subtitles") or {}
    autos = {k: v for k, v in (info.get("automatic_captions") or {}).items()
             if not is_translation_variant(k)}

    for lang in _lang_variants(orig, subs.keys()):
        if lang in subs:
            return lang, "official", True
    for lang in _lang_variants(orig, autos.keys()):
        if lang in autos:
            return lang, "auto", True
    # 原语言未知（info.language 缺失）时，白名单命中的就是"我们能拿到的最好字幕"，
    # 没有证据说明它是译本，不能据此判成翻译并强推 ASR。
    assumed_orig = not orig
    for lang in cfg["sub_langs"]:
        if lang in subs:
            return lang, "official", assumed_orig
    for lang in cfg["sub_langs"]:
        if lang in autos:
            return lang, "auto", assumed_orig
    return None, None, False


def fetch(url: str, cfg: dict, jobdir: Path) -> dict:
    if LIST_URL_RE.search(url):
        raise FetchError("stage0", "playlist_url", "这是播放列表/合集链接，只支持单条视频",
                         hint="去掉 list/index 参数，或直接贴视频页 URL")
    vid = video_id(url)
    sid = f"youtube:{vid}"
    raw = jobdir / "raw"
    raw.mkdir(parents=True, exist_ok=True)

    info = _info(url, cfg)
    live = info.get("live_status") or "not_live"
    if live in ("is_live", "is_upcoming"):
        raise FetchError("stage1", "live_stream", f"直播中/预告（live_status={live}）",
                         hint="直播结束后再跑")
    if not info.get("duration"):
        raise FetchError("stage1", "no_duration", "拿不到时长")

    duration = float(info["duration"])
    lang, tsource, is_orig = pick_language(info, cfg)
    sub_path = None
    audio_path = None

    if lang:
        flag = "--write-subs" if tsource == "official" else "--write-auto-subs"
        p = _ytdlp(cfg, [flag, "--skip-download", "--sub-langs", lang,
                         "--sub-format", "vtt", "-P", str(raw),
                         "-o", "%(id)s.%(ext)s", url], timeout=180)
        cands = sorted(glob.glob(str(raw / f"*{lang}*.vtt"))) or sorted(glob.glob(str(raw / "*.vtt")))
        if p.returncode == 0 and cands:
            sub_path = cands[0]
        else:
            lang, tsource = None, None

    # R9：底稿必须保留原语言。若只有翻译稿，宁可走 ASR 也不拿译文当底稿。
    if sub_path and not is_orig:
        print(f"[warn] 只有 {lang} 翻译字幕（原语言 {info.get('language')}），改走 ASR 以保住底稿语言",
              file=sys.stderr)
        sub_path = None
        lang, tsource, is_orig = None, None, False
        audio_path = _download_audio(url, cfg, raw)

    if not sub_path and not audio_path:
        audio_path = _download_audio(url, cfg, raw)

    job = {
        "source_id": sid,
        "source_url": f"https://www.youtube.com/watch?v={vid}",
        "video_id": vid,
        "title": info.get("title") or vid,
        "channel": info.get("channel") or info.get("uploader"),
        "duration": duration,
        "live_status": live,
        "was_live": live == "was_live",
        "lang": lang or cfg.get("asr_lang") or "und",
        "transcript_source": tsource,
        "sub_path": sub_path,
        "audio_path": audio_path,
        "has_manual_subs": bool(info.get("subtitles")),
        "orig_language": info.get("language"),
        "sub_lang_is_original": is_orig,
        "formats_degraded": None,
        "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    (jobdir / "job.json").write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")
    return job


def _download_audio(url: str, cfg: dict, raw: Path) -> str:
    p = _ytdlp(cfg, ["-f", "bestaudio/best", "-x", "--audio-format", "m4a",
                     "--audio-quality", "0", "-P", str(raw),
                     "-o", "audio.%(ext)s", url], timeout=1800)
    cands = sorted(glob.glob(str(raw / "audio.*")))
    if p.returncode != 0 or not cands:
        err = (p.stderr or "").strip()
        last = err.splitlines()[-1] if err else ""
        if "403" in err or "Forbidden" in err:
            raise FetchError(
                "stage1", "audio_forbidden",
                "YouTube 拒绝下载音轨（403）", cause=last,
                hint="常见于挑战未解或需要登录：先确认 --remote-components 生效，"
                     "或配置 cookie；也可换一条有字幕的样本")
        raise FetchError("stage1", "audio_failed", "既没有可用字幕，音频也下载失败",
                         cause=last, hint="确认不是会员/地区限制")
    return cands[0]
