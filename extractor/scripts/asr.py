"""ASR 兜底（mlx-whisper）。ASR 层可替换：只要产出 Cue 列表即可。"""
from __future__ import annotations

import os

from subs import Cue


def _apply_net_env(cfg: dict) -> None:
    """huggingface_hub 只认 HTTP(S)_PROXY，不认我们的配置——模型下载必须显式注入。"""
    proxy = cfg.get("proxy")
    if proxy:
        for k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
            os.environ[k] = proxy
            os.environ[k.lower()] = proxy
    endpoint = cfg.get("hf_endpoint")
    if endpoint:
        os.environ["HF_ENDPOINT"] = endpoint
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")


class ASRError(RuntimeError):
    """带原因码的 ASR 失败（SPEC §13 要求分阶段报错）。"""

    def __init__(self, code: str, message: str, cause: str = "", hint: str = ""):
        self.code, self.message, self.cause, self.hint = code, message, cause, hint
        super().__init__(f"[stage2.asr] {code}: {message}")

    def as_dict(self) -> dict:
        return {"stage": "stage2", "code": self.code, "message": self.message,
                "cause": self.cause, "hint": self.hint}


def available() -> bool:
    try:
        import mlx_whisper  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


def transcribe(path: str, cfg: dict) -> tuple[list[Cue], str]:
    _apply_net_env(cfg)
    try:
        import mlx_whisper
    except ImportError:
        raise ASRError("not_installed", "未安装 mlx-whisper",
                       hint="bash setup_env.sh --with-asr") from None

    try:
        result = mlx_whisper.transcribe(
            path,
            path_or_hf_repo=cfg["whisper_model"],
            language=cfg.get("asr_lang"),
            verbose=False,
            word_timestamps=True,   # 句内时间点：否则一条长 cue 内部无法定锚
        )
    except Exception as e:  # noqa: BLE001
        low = f"{type(e).__name__} {e}".lower()
        if "connection refused" in low or "connecterror" in low or "offline" in low \
                or "localentrynotfound" in low:
            raise ASRError(
                "model_unavailable",
                f"需要下载 ASR 模型（{cfg['whisper_model']}），但当前无法连到 HuggingFace",
                cause=str(e).splitlines()[0][:200],
                hint="代理是按需启用的：先开代理再重跑；模型只需下载一次",
            ) from None
        if "out of memory" in low or "memory" in low:
            raise ASRError("oom", "内存不足", cause=str(e).splitlines()[0][:200],
                           hint="换小模型：mlx-community/whisper-small") from None
        raise ASRError("transcribe_failed", "ASR 转写失败",
                       cause=str(e).splitlines()[0][:200]) from None

    lang = result.get("language") or "und"
    segs = result.get("segments") or []
    if not segs:
        raise ASRError("no_speech", "ASR 没有识别出任何语音",
                       hint="确认音轨有实际人声；纯音乐应判 no-speech")
    # 优先用词级时间戳：否则 10 秒长句内部无从切分，锚点只能落在句首
    cues: list[Cue] = []
    for seg in segs:
        words = seg.get("words") or []
        if words:
            for w in words:
                raw = str(w.get("word", ""))
                cues.append(Cue(float(w["start"]), float(w["end"]), raw,
                                lead=raw[:1].isspace()))
        else:
            cues.append(Cue(float(seg["start"]), float(seg["end"]), seg["text"]))
    return cues, lang
