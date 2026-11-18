# OIRO Desktop Shell

Electron 壳层启动本机 FastAPI（SQLite 在用户数据目录），加载既有 Vue 前端。

## 开发

```bash
# 终端 1 — 后端（源码模式，无安装密钥）
cd /workspace
make dev

# 终端 2 — 仅 Electron 窗口（指向 Vite）
cd desktop && npm install && OIRO_DEV_URL=http://127.0.0.1:5173 npm start
```

从源码直接 `electron .` 或 `npm start` 时，`OIRO_DISTRIBUTION` 默认为 **`source`**（与 Python 后端一致），不会意外启用安装密钥门闩。仅正式打包产物默认为 `packaged`。

测试**正式包安装密钥门闩**：

```bash
OIRO_DISTRIBUTION=packaged OIRO_INSTALL_KEY_ACCEPT=OIRO-TEST-0000-0000-0000 bash scripts/dev.sh
```

## Node 单元测试（路径 / Python 解析）

```bash
cd desktop && npm test
```

## Windows 打包与运行

在 **Windows** 上用 PowerShell 执行（无需 `make`）：

```powershell
git clone https://github.com/fenglan2010/Orio-Desktop.git Orio-Desktop
cd Orio-Desktop
python -m venv .venv
.\.venv\Scripts\pip install -r backend\requirements.txt
cd frontend
npm ci
npm run build
cd ..\desktop
npm ci
$env:CSC_IDENTITY_AUTO_DISCOVERY = "false"
$env:OIRO_DISTRIBUTION = "packaged"
npm run pack:dir
```

产物目录：`desktop\dist\win-unpacked\`。运行 `OIRO.exe`。

打包后验收（PowerShell，在仓库根目录执行）：

```powershell
$root = "desktop\dist\win-unpacked"
Test-Path (Join-Path $root "resources\renderer\index.html")
Test-Path (Join-Path $root "resources\backend\app\main.py")
```

两者均应返回 `True`。

**前端静态资源**：`npm run pack:dir` / `pack:win` 会先检查 `frontend\dist\index.html`；缺少则直接失败。打包后前端位于 **`resources\renderer`**（与 `resources\backend` 同级），供 Python/uvicorn 读取。不要只靠 asar 内的路径——Python 无法读 `app.asar`，会导致窗口空白且 `http://127.0.0.1:8765/` 返回 `{"detail":"Not Found"}`。

**Python 查找顺序（Windows）**：向上查找项目根 `.venv\Scripts\python.exe`（例如克隆目录 `Orio-Desktop\.venv`）→ 本机 `python` → `py -3` → `python3`。打包应用不会在内置 asar 里找 venv；后端代码位于 `resources\backend`（`extraResources`）。

若未找到 Python，应用弹出中文错误对话框，而不是 `spawn python3 ENOENT` 崩溃。

一键脚本（含前端构建）：

```powershell
.\desktop\scripts\build-windows.ps1
```

`CSC_IDENTITY_AUTO_DISCOVERY=false` 用于避免未签名构建时解压 `winCodeSign` 失败。若你自行启用代码签名且遇到符号链接权限错误，请以管理员身份运行 PowerShell 或开启 Windows **开发人员模式**。

正式安装包设置 `OIRO_DISTRIBUTION=packaged`；开源从源码运行保持默认 `source`，无需安装密钥。