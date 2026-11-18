# OIRO Desktop Shell

Electron 壳层在本机启动 FastAPI（SQLite 位于用户数据目录），加载 Vue 前端。**酒店生产数据**来自设置中的 OTA 官方契约路径、PMS API 与渠道账号，而非长期依赖仓库演示种子。

仓库：**https://github.com/fenglan2010/Orio-Desktop**

## 酒店 Windows 主线（PowerShell）

```powershell
git clone https://github.com/fenglan2010/Orio-Desktop.git Orio-Desktop
cd Orio-Desktop

python -m venv .venv
.\.venv\Scripts\pip install -r backend\requirements.txt

# Node 20 LTS 推荐；Node 26+ 可能导致 build/pack 失败
$env:CSC_IDENTITY_AUTO_DISCOVERY = "false"
.\desktop\scripts\build-windows.ps1              # 绿色版 win-unpacked
.\desktop\scripts\build-windows.ps1 -WithInstaller  # 额外生成未签名 NSIS
```

首次运行 `OIRO.exe` → 输入安装密钥 **`OIRO-XXXX-XXXX-XXXX`**（三段）→ 登录 → 按 [docs/配置指南.md](../docs/配置指南.md) 配置模型密钥与 OTA/PMS。

完整说明：[docs/安装指南.md](../docs/安装指南.md) · [docs/操作运行指南.md](../docs/操作运行指南.md)

### 打包验收

```powershell
$root = "desktop\dist\win-unpacked"
Test-Path (Join-Path $root "resources\renderer\index.html")   # True
Test-Path (Join-Path $root "resources\backend\app\main.py")   # True
```

### 前端必须在 `resources\renderer`

`npm run pack:dir` / `pack:win` / `pack:win:nsis` 会先执行 `ensure-frontend-dist.js --build`（缺 `frontend\dist\index.html` 时自动构建，仍失败则 loud exit）。打包产物将前端放在 **`resources\renderer`**（`extraResources`），后端在 **`resources\backend`**。Python **不能**读 `app.asar` 内静态资源。

典型故障：**窗口空白** + `http://127.0.0.1:8765/` 返回 `{"detail":"Not Found"}` → 未正确打入 `resources\renderer`，需重新构建前端并打包。

### Python 查找（Windows）

向上查找仓库根 `.venv\Scripts\python.exe` → 系统 `python` → `py -3` → `python3`。缺 Python 时弹中文对话框，而非 `spawn python3 ENOENT`。

### 未签名构建

- `CSC_IDENTITY_AUTO_DISCOVERY=false`（`build-windows.ps1` 已设置）避免 `winCodeSign` 符号链接权限问题。  
- `electron-builder` 配置 `signAndEditExecutable: false`。  
- SmartScreen「未知发布者」为未签名包正常现象。

### CI 制品

推送后 **Actions → Desktop Windows pack smoke** 上传：

- `oiro-win-unpacked` — 绿色版目录 zip  
- `oiro-setup-exe` — 若 NSIS 步骤成功，含 `OIRO-Setup-*.exe`

## 开发（非酒店主线）

```bash
# 终端 1 — 后端（源码模式，无安装密钥）
make dev

# 终端 2 — Electron 指向 Vite
cd desktop && npm install && OIRO_DEV_URL=http://127.0.0.1:5173 npm start
```

`OIRO_DISTRIBUTION` 默认为 **`source`**，不启用安装密钥门闩。

**模拟正式包门闩（开发）** — 密钥须符合 `OIRO-XXXX-XXXX-XXXX`：

```bash
export OIRO_DISTRIBUTION=packaged
export OIRO_INSTALL_KEY_ACCEPT=OIRO-TEST-0000-0000
bash scripts/dev.sh
```

## npm 脚本

| 命令 | 作用 |
|------|------|
| `npm run build:frontend` | 构建 `../frontend/dist` |
| `npm run pack:dir` | 构建前端 + `electron-builder --dir` |
| `npm run pack:win:nsis` | 构建前端 + 未签名 NSIS |
| `npm run pack:win` | 构建前端 + `dir` + `nsis`（electron-builder win 目标） |

## Node 单元测试

```bash
cd desktop && npm test
```