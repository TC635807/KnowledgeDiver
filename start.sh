#!/bin/bash

echo "========================================"
echo "  KnowledgeDiver - Starting Services"
echo "========================================"
echo

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ ! -f "$SCRIPT_DIR/.env" ]; then
  echo "提示: 未找到 $SCRIPT_DIR/.env，将以代码默认值启动。"
  echo "      建议先执行: cp .env.example .env 并设置 JWT_SECRET。"
  echo
fi

if [ ! -d "$SCRIPT_DIR/.venv" ]; then
  echo "Creating virtual environment..."
  python3 -m venv "$SCRIPT_DIR/.venv"
fi

source "$SCRIPT_DIR/.venv/bin/activate"

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

if [ -n "$START_INSTALL_DEPS" ] || ! python -c "import uvicorn" 2>/dev/null; then
  echo "Installing backend dependencies (using mirror)..."
  # 两步安装避免 sentence-transformers 依赖解析超时
  pip install -i https://pypi.tuna.tsinghua.edu.cn/simple -r "$SCRIPT_DIR/requirements.txt" --no-deps 2>/dev/null || true
  pip install -i https://pypi.tuna.tsinghua.edu.cn/simple -r "$SCRIPT_DIR/requirements.txt"
else
  echo "Backend dependencies already installed, skip (set START_INSTALL_DEPS=1 to force)"
fi

echo "Installing Playwright browsers for crawl4ai..."
python3 - "$SCRIPT_DIR" << 'PYEOF' || true
import json, os, sys, urllib.request, zipfile
from pathlib import Path

script_dir = sys.argv[1]
browsers_path = os.environ.get("PLAYWRIGHT_BROWSERS_PATH",
    os.path.join(script_dir, ".playwright"))
os.makedirs(browsers_path, exist_ok=True)

pyver = f"python{sys.version_info.major}.{sys.version_info.minor}"
driver_pkg = os.path.join(script_dir, ".venv", "lib", pyver,
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

if [ ! -d "node_modules" ]; then
  echo "Installing frontend dependencies..."
  npm install --registry=https://registry.npmmirror.com
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
