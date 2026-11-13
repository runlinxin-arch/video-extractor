"""生成合成字幕夹具。

为什么不用真实字幕：
  1. 版权——真实 YouTube 字幕是受版权保护的内容，不能随仓库分发
  2. 稳定——真实视频会下架、字幕会变，断言就飘了

但真实数据里踩到的坑全部保留在这份生成器里：
  · 滚动窗口重复（相邻 cue 是"尾部重叠"，不是相同文本）
  · 10ms 的碎 cue（其中一些**承载新词**，不能按时长丢弃）
  · `<c>` 与 `<00:00:xx>` 内嵌标签
  · 说话人标记 `>>`
  · 空 cue
"""
from __future__ import annotations

import random

WORDS = (
    "attention focus phone habit sleep reward trigger loop system brain cortex "
    "energy meeting screen scroll notification email team goal deadline project "
    "learning memory practice signal noise friction design default setting "
    "measure baseline rhythm sprint break recovery study result pattern"
).split()

CJK = "注意力习惯回路奖赏触发睡眠节律专注冲刺摩擦默认设置信号噪声"


def _ts(sec: float) -> str:
    ms = int(round(sec * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


def build(duration_s: float = 3600.0, n_cues: int = 3000, seed: int = 7) -> str:
    rnd = random.Random(seed)
    out = ["WEBVTT", "Kind: captions", "Language: en", ""]
    step = duration_s / n_cues

    window: list[str] = []          # 当前滑动窗口里的词
    t = 0.0
    cap_next = True                 # 句首大写（真实字幕如此，否则 ASCII 句号不当边界）
    speaker = 0
    for i in range(n_cues):
        t_next = (i + 1) * step

        # 每个 cue 都推进窗口：新词进、旧词出（实测约 2.5–3.5× 重复，贴近真实数据）
        add = [rnd.choice(WORDS) for _ in range(rnd.randint(3, 5))]
        if cap_next:
            add[0] = add[0][:1].upper() + add[0][1:]   # 句号后的第一个词大写
            cap_next = False
        window.extend(add)
        # 每 3 个 cue 收一次句，模拟带标点字幕（否则只能按 40 词硬切）
        if i % 3 == 2:
            window[-1] = window[-1] + "."
            cap_next = True
        while len(window) > 12:
            window.pop(0)

        text = " ".join(window)

        # 制造各种"真实脏数据"
        if i % 97 == 0:
            out += [f"{_ts(t)} --> {_ts(t + 0.01)}", text, ""]        # 10ms 碎 cue（有内容）
        elif i % 61 == 0:
            out += [f"{_ts(t)} --> {_ts(t + 0.01)}", "", ""]          # 空 cue
        elif i % 53 == 0:
            out += [f"{_ts(t)} --> {_ts(t_next)}", "", ""]            # 空 cue（长）
        elif i % 41 == 0:
            speaker += 1
            out += [f"{_ts(t)} --> {_ts(t_next)}", f">> {text}", ""]  # 说话人切换
        elif i % 29 == 0:
            # 内嵌标签：把 " ".join 换成逐词 caret 标签
            tagged = "".join(
                f"<{_ts(t + k * 0.1)}><c> {w}</c>" for k, w in enumerate(window[:3])
            ) + " " + " ".join(window[3:])
            out += [f"{_ts(t)} --> {_ts(t_next)}", tagged.strip(), ""]
        else:
            out += [f"{_ts(t)} --> {_ts(t_next)}", text, ""]

        t = t_next
    return "\n".join(out)


def write(path: str) -> str:
    with open(path, "w", encoding="utf-8") as f:
        f.write(build())
    return path


if __name__ == "__main__":
    import sys

    write(sys.argv[1] if len(sys.argv) > 1 else "synthetic.en.vtt")
