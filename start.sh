#!/bin/bash

echo "========================================"
echo "  KnowledgeDiver - Starting Services"
echo "========================================"
echo

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# .env 不存在时自动从模板生成并写入随机 JWT_SECRET（否则 backend/config.py 会直接抛错退出）
if [ ! -f "$SCRIPT_DIR/.env" ]; then
  echo "未找到 $SCRIPT_DIR/.env，正在从 .env.example 自动生成..."
  if [ -f "$SCRIPT_DIR/.env.example" ]; then
    cp "$SCRIPT_DIR/.env.example" "$SCRIPT_DIR/.env"
    SECRET="$(python3 -c 'import secrets; print(secrets.token_hex(32))' 2>/dev/null || openssl rand -hex 32)"
    if grep -q '^JWT_SECRET=' "$SCRIPT_DIR/.env"; then
      sed -i.bak "s|^JWT_SECRET=.*|JWT_SECRET=$SECRET|" "$SCRIPT_DIR/.env" && rm -f "$SCRIPT_DIR/.env.bak"
    else
      printf '\nJWT_SECRET=%s\n' "$SECRET" >> "$SCRIPT_DIR/.env"
    fi
    chmod 600 "$SCRIPT_DIR/.env"
    echo "  ✅ 已生成 .env 并写入随机 JWT_SECRET"
    echo "  ⚠️  要生成卡片 / 使用 Agent，请编辑 .env 填入 AI_API_KEY"
  else
    echo "  ❌ 缺少 .env.example，请手动创建 .env 并设置 JWT_SECRET"
  fi
  echo
fi

# 虚拟环境位置：默认 <项目>/.venv；可用 VENV_DIR 指向已有环境以复用、避免重新下载依赖
VENV_DIR="${VENV_DIR:-$SCRIPT_DIR/.venv}"
PIP_INDEX="${PIP_INDEX:-https://pypi.tuna.tsinghua.edu.cn/simple}"

if [ ! -d "$VENV_DIR" ]; then
  echo "Creating virtual environment at $VENV_DIR ..."
  python3 -m venv "$VENV_DIR"
fi

source "$VENV_DIR/bin/activate"

if [ -n "$ALL_PROXY" ]; then
  echo "Fixing proxy: $ALL_PROXY -> ${ALL_PROXY/socks:\/\//socks5:\/\/}"
  ALL_PROXY="${ALL_PROXY/socks:\/\//socks5:\/\/}"
  export ALL_PROXY
fi
if [ -n "$all_proxy" ]; then
  all_proxy="${all_proxy/socks:\/\//socks5:\/\/}"
  export all_proxy
fi

# Playwright 浏览器装到项目目录（不设 DOWNLOAD_HOST——npmmirror 路径与 Playwright 1.58+ 的 builds/cft/ 硬编码不兼容）
export PLAYWRIGHT_BROWSERS_PATH="$SCRIPT_DIR/.playwright"

# 依赖安装策略：
#   * 以 requirements.txt 的哈希作为「已安装完成」标记，标记匹配就完全跳过安装（不再重复下载）；
#   * 只有完整安装 + 关键传递依赖 import 校验都通过才写标记，避免「半装」被误判成装好；
#   * venv 只装了顶层包（--no-deps 那一遍）时不会留标记，但下次仍会重试而不是静默跳过；
#   * START_INSTALL_DEPS=1 强制重装；VENV_DIR=/path/to/.venv 复用已有环境（可做到零下载）。
DEPS_HASH="$(sha256sum "$SCRIPT_DIR/requirements.txt" | cut -c1-16)"
DEPS_MARKER="$VENV_DIR/.deps-$DEPS_HASH.ok"

if [ -n "$START_INSTALL_DEPS" ] || [ ! -f "$DEPS_MARKER" ]; then
  echo "Installing backend dependencies from $PIP_INDEX ..."

  # 本项目的嵌入模型只用 CPU 推理，但 PyPI 的 linux torch 是 CUDA 版（会拉约 2.7GB 的 nvidia-* 依赖）。
  # 这里先装 CPU 版 torch（约 180-200MB）；随后 -r requirements.txt 因 torch>=1.11.0 已满足会跳过它。
  # 需要 GPU 版：TORCH_CPU_ONLY=0
  if [ "${TORCH_CPU_ONLY:-1}" = "1" ] && ! python -c "import torch" 2>/dev/null; then
    echo "Installing CPU-only PyTorch (avoid ~2.7GB CUDA deps; GPU users: TORCH_CPU_ONLY=0)..."
    python -m pip install --index-url "${TORCH_CPU_INDEX:-https://download.pytorch.org/whl/cpu}" torch \
      || echo "  ⚠️  CPU 版 torch 安装失败，将回退默认源（会拉 CUDA 依赖）"
  fi
  # 两步安装：先装顶层包（跳过依赖解析，避免 sentence-transformers 解析超时），再补齐传递依赖
  python -m pip install -i "$PIP_INDEX" -r "$SCRIPT_DIR/requirements.txt" --no-deps 2>/dev/null || true
  if ! python -m pip install -i "$PIP_INDEX" -r "$SCRIPT_DIR/requirements.txt"; then
    echo
    echo "❌ 后端依赖安装失败（网络中断或依赖冲突），本次不写安装标记。"
    echo "   手动修复后重跑即可："
    echo "   $VENV_DIR/bin/pip install -i $PIP_INDEX -r $SCRIPT_DIR/requirements.txt"
    exit 1
  fi
  # 校验关键传递依赖真的能 import（防止 --no-deps 半装状态被当成安装完成）
  if ! python - "$DEPS_HASH" "$DEPS_MARKER" <<'PYEOF'
import importlib.util, pathlib, sys

missing = [m for m in ("uvicorn", "fastapi", "click", "starlette", "pydantic_core", "httpx")
           if importlib.util.find_spec(m) is None]
if missing:
    print("  ❌ 依赖校验失败，仍缺少：" + ", ".join(missing))
    sys.exit(1)
pathlib.Path(sys.argv[2]).write_text(sys.argv[1], encoding="utf-8")
print("  ✅ 依赖校验通过，写入安装标记 " + sys.argv[2])
PYEOF
  then
    echo "❌ venv 仍处于半装状态（缺少传递依赖）。请重跑，或 START_INSTALL_DEPS=1 强制重装。"
    exit 1
  fi
else
  echo "Backend dependencies already installed (requirements.txt@$DEPS_HASH), skip install."
  echo "  (强制重装: START_INSTALL_DEPS=1 ；复用其它 venv: VENV_DIR=/path/to/.venv)"
fi

echo "Installing Playwright browsers for crawl4ai..."
python3 - "$SCRIPT_DIR" "$VENV_DIR" << 'PYEOF' || true
import json, os, sys, urllib.request, zipfile
from pathlib import Path

script_dir = sys.argv[1]
venv_dir = sys.argv[2] if len(sys.argv) > 2 else os.path.join(script_dir, ".venv")
browsers_path = os.environ.get("PLAYWRIGHT_BROWSERS_PATH",
    os.path.join(script_dir, ".playwright"))
os.makedirs(browsers_path, exist_ok=True)

pyver = f"python{sys.version_info.major}.{sys.version_info.minor}"
driver_pkg = os.path.join(venv_dir, "lib", pyver,
    "site-packages", "playwright", "driver", "package")
browsers_json = os.path.join(driver_pkg, "browsers.json")
if not os.path.exists(browsers_json):
    print("  ⚠️  playwright browsers.json not found, skipping")
    sys.exit(0)

data = json.load(open(browsers_json))
browsers = {b["name"]: b for b in data.get("browsers", []) if b.get("installByDefault")}

NEEDED = {
    "chromium": (
        "chromium-{rev}", "chrome-linux64", "chrome",
        "https://cdn.npmmirror.com/binaries/chrome-for-testing/{ver}/linux64/chrome-linux64.zip",
        "https://cdn.playwright.dev/builds/cft/{ver}/linux64/chrome-linux64.zip"
    ),
    "chromium-headless-shell": (
        "chromium_headless_shell-{rev}", "chrome-headless-shell-linux64", "chrome-headless-shell",
        "https://cdn.npmmirror.com/binaries/chrome-for-testing/{ver}/linux64/chrome-headless-shell-linux64.zip",
        "https://cdn.playwright.dev/builds/cft/{ver}/linux64/chrome-headless-shell-linux64.zip"
    ),
    "ffmpeg": (
        "ffmpeg-{rev}", ".", None,
        None,
        "https://cdn.playwright.dev/builds/ffmpeg/{rev}/ffmpeg-linux.zip"
    ),
}

all_ok = True
for bname, (dname, subdir, binary, mirror_url, fallback_url) in NEEDED.items():
    if bname not in browsers:
        continue
    b = browsers[bname]
    rev = b["revision"]
    ver = b.get("browserVersion", "")
    dir_name = dname.format(rev=rev)
    install_dir = os.path.join(browsers_path, dir_name)
    marker = os.path.join(install_dir, "INSTALLATION_COMPLETE")

    if os.path.exists(marker) and (binary is None or os.path.isfile(os.path.join(install_dir, subdir, binary))):
        print(f"  ✅ {bname} (rev {rev}) — already installed")
        continue

    tmp_zip = os.path.join(browsers_path, f"{bname}.zip")
    downloaded = False

    for label, url in [("npmmirror", mirror_url), ("official CDN", fallback_url)]:
        if url is None:
            continue
        url_fmt = url.format(rev=rev, ver=ver)
        try:
            print(f"  ⬇️  Downloading {bname} from {label}...")
            urllib.request.urlretrieve(url_fmt, tmp_zip)
            downloaded = True
            break
        except Exception as e:
            print(f"     {label} failed: {e}")
            continue

    if not downloaded:
        print(f"  ❌ {bname}: all download sources failed")
        all_ok = False
        continue

    os.makedirs(os.path.join(install_dir, subdir), exist_ok=True)
    with zipfile.ZipFile(tmp_zip, "r") as zf:
        zf.extractall(install_dir)
    os.remove(tmp_zip)

    Path(marker).touch()
    if binary:
        bin_path = os.path.join(install_dir, subdir, binary)
        if os.path.exists(bin_path):
            os.chmod(bin_path, 0o755)

    print(f"  ✅ {bname} (rev {rev}) — installed")

if all_ok:
    print("  All browsers ready.")
PYEOF

# 嵌入模型自检：缺失或不完整则自动下载（约 92MB；国内默认走 hf-mirror 镜像）
MODEL_DIR="${SCRIPT_DIR}/models/bge-small-zh-v1.5"
if [ -f "$MODEL_DIR/config.json" ] && [ -f "$MODEL_DIR/model.safetensors" ] && [ -f "$MODEL_DIR/tokenizer.json" ]; then
  echo "Embedding model ready, skip download ($MODEL_DIR)"
else
  echo "Downloading embedding model BAAI/bge-small-zh-v1.5 (~92MB)..."
  python - "$MODEL_DIR" <<'PYEOF' || echo "  ⚠️ 嵌入模型自动下载失败：程序首次使用时会回退到在线 HF 名称（需联网）；也可按 README 手动下载。"
import os, sys

repo = os.environ.get("HF_MODEL_REPO", "BAAI/bge-small-zh-v1.5")
dest = sys.argv[1]
endpoints = [os.environ.get("HF_ENDPOINT") or "https://hf-mirror.com"]
if "https://huggingface.co" not in endpoints:
    endpoints.append("https://huggingface.co")

last_err = None
for endpoint in endpoints:
    os.environ["HF_ENDPOINT"] = endpoint
    try:
        from huggingface_hub import snapshot_download

        print("  from %s ..." % endpoint)
        snapshot_download(
            repo_id=repo,
            local_dir=dest,
            # 跳过冗余的 pytorch_model.bin（transformers 优先用 safetensors），省约 90MB
            ignore_patterns=["*.bin", "*.h5", "*.msgpack", "*.onnx", "*.tflite", "*.ot"],
        )
        print("  ✅ model ready: %s" % dest)
        sys.exit(0)
    except Exception as exc:
        last_err = exc
        print("  failed: %s" % exc)

print("  all endpoints failed: %s" % last_err)
print("  可手动下载：HF_ENDPOINT=https://hf-mirror.com 或见 README「快速开始」第 2 步")
sys.exit(1)
PYEOF
fi

if ! command -v uvicorn &> /dev/null; then
  echo "Error: uvicorn installation failed"
  read -p "Press Enter to exit..."
  exit 1
fi

echo "[1/2] Starting Backend..."
cd "$SCRIPT_DIR"
export PYTHONPATH="$SCRIPT_DIR"
uvicorn backend.main:app --reload --port 8000 &
BACKEND_PID=$!

sleep 2

echo "[2/2] Starting Frontend..."
cd "$SCRIPT_DIR/frontend"

if [ ! -x "node_modules/.bin/vite" ]; then
  echo "Installing frontend dependencies (node_modules 缺失或不完整)..."
  npm install --registry="${NPM_REGISTRY:-https://registry.npmmirror.com}"
fi

if [ ! -f "node_modules/.bin/vite" ]; then
  echo "Error: vite not found, npm install may have failed"
  read -p "Press Enter to exit..."
  kill $BACKEND_PID 2>/dev/null
  exit 1
fi

npm run dev &
FRONTEND_PID=$!

echo
echo "========================================"
echo "  Services started!"
echo "  Backend:  http://localhost:8000"
echo "  Frontend: http://localhost:3000"
echo "========================================"
echo
echo "Press Ctrl+C to stop all services..."
echo

trap "echo 'Stopping services...'; kill $BACKEND_PID $FRONTEND_PID 2>/dev/null; exit 0" SIGINT SIGTERM

wait
