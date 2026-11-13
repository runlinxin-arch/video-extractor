#!/usr/bin/env bash
# 建 venv 与依赖。默认只装核心（pyyaml）；ASR 可选。
# 用法： bash setup_env.sh [--with-asr]
set -euo pipefail
cd "$(dirname "$0")"

WITH_ASR=0
[ "${1:-}" = "--with-asr" ] && WITH_ASR=1

if ! command -v uv >/dev/null 2>&1; then
  echo "需要 uv（brew install uv 或 curl -LsSf https://astral.sh/uv/install.sh | sh）" >&2
  exit 1
fi

uv venv --python 3.12 .venv
.venv/bin/python -m pip --version >/dev/null 2>&1 || true
uv pip install --python .venv/bin/python -r requirements.txt

if [ "$WITH_ASR" = "1" ]; then
  echo "… 安装 mlx-whisper（会下载模型权重，首次转写时另下载 ~1.5GB）"
  uv pip install --python .venv/bin/python mlx-whisper
fi

echo
echo "环境就绪。检查前端："
echo "  ./.venv/bin/python scripts/extract.py preflight"
echo "外部依赖：yt-dlp（必装）、ffmpeg（ASR 需合并音轨）、node（yt-dlp 解 JS 挑战）"
for c in yt-dlp ffmpeg node; do
  if command -v "$c" >/dev/null 2>&1; then echo "  ✅ $c"; else echo "  ❌ $c 未安装"; fi
done
