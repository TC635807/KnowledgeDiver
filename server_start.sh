#!/bin/bash
set -e

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
echo "========================================"
echo "  KnowledgeDiver - 更新并启动服务"
echo "  项目目录: $APP_DIR"
echo "========================================"
echo

# 1. Install backend dependencies then restart
echo "[1/5] 安装后端依赖..."
if [ -f "$APP_DIR/requirements.txt" ]; then
    # 先装已有依赖（跳过解析），再完整安装确保传递依赖不漏
    "$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/requirements.txt" --no-deps 2>/dev/null || true
    "$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/requirements.txt"
    echo "  依赖安装完成"
    # Playwright 浏览器装到项目目录（不设 DOWNLOAD_HOST——npmmirror 路径与 Playwright 1.58+ 的 builds/cft/ 硬编码不兼容）
    export PLAYWRIGHT_BROWSERS_PATH="$APP_DIR/.playwright"
    echo "  安装 Playwright 浏览器..."
    "$APP_DIR/.venv/bin/python" - "$APP_DIR" << 'PYEOF' || true
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
    print("  Playwright 浏览器安装完成.")
PYEOF
else
    echo "  requirements.txt 不存在，跳过"
fi

# 1.5 检验 Playwright 环境（依赖可导入 + Chromium 真实可启动）
echo "  检验 Playwright 环境..."
export PLAYWRIGHT_BROWSERS_PATH="$APP_DIR/.playwright"
if "$APP_DIR/.venv/bin/python" - << 'PYEOF' 2>&1
try:
    import crawl4ai  # noqa: F401
    from playwright.sync_api import sync_playwright

    print("  ✅ crawl4ai / playwright 可导入")
    p = sync_playwright().start()
    b = p.chromium.launch(headless=True)
    b.close()
    p.stop()
    print("  ✅ Chromium 启动成功")
except Exception as e:
    print(f"  ❌ Playwright 检验失败: {e}")
    raise SystemExit(1)
PYEOF
then
    echo "  Playwright 环境正常"
else
    echo "  ⚠️ Playwright 环境异常——请检查系统依赖（libnss3/libatk1.0/libgbm 等）后重跑本脚本"
fi

# 1.6 检查 systemd 服务是否配置浏览器路径（脚本内 export 不会传给 systemd 服务）
if systemctl cat knowledgediver 2>/dev/null | grep -q "PLAYWRIGHT_BROWSERS_PATH"; then
    echo "  ✅ systemd 已配置 PLAYWRIGHT_BROWSERS_PATH"
else
    echo "  ⚠️ systemd 服务未配置 PLAYWRIGHT_BROWSERS_PATH——后端将找不到项目内浏览器，请添加:"
    echo "     [Service] 段内加一行: Environment=PLAYWRIGHT_BROWSERS_PATH=$APP_DIR/.playwright"
fi

echo "[2/5] 重启后端..."
systemctl restart knowledgediver
echo "  后端已重启"

# 2. Build frontend
echo "[3/5] 构建前端..."
FRONTEND_DIR="$APP_DIR/frontend"
if [ -d "$FRONTEND_DIR" ]; then
    cd "$FRONTEND_DIR"
    npm install --silent
    npm run build
    echo "  前端构建完成"
else
    echo "  前端目录不存在: $FRONTEND_DIR，跳过前端构建"
    exit 1
fi

# 3. Restart frontend
echo "[4/5] 重启前端..."
if systemctl is-active --quiet knowledgediver-frontend 2>/dev/null; then
    systemctl restart knowledgediver-frontend
    echo "  前端已重启（systemd）"
elif systemctl is-enabled knowledgediver-frontend &>/dev/null; then
    systemctl start knowledgediver-frontend
    echo "  前端已启动（systemd）"
else
    # Fallback: start directly if service not installed
    echo "  systemd 服务未安装，直接启动..."
    cd "$FRONTEND_DIR"
    nohup npm run preview -- --host 0.0.0.0 --port 5173 > /dev/null 2>&1 &
    echo "  前端已启动（直接运行，PID: $!）"
fi

# 4. Reload nginx
echo "[5/5] 重载 Nginx..."
if command -v nginx &> /dev/null; then
    systemctl reload nginx 2>/dev/null || systemctl restart nginx 2>/dev/null || true
    echo "  Nginx 已重载"
else
    echo "  Nginx 未安装，跳过"
fi

echo
echo "========================================"
echo "  服务已更新并启动"
echo "  后端: http://localhost:8000"
echo "  网站: https://knowledgediver.cloud"
echo "========================================"
