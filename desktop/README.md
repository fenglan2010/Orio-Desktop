# OIRO Desktop Shell

Electron 壳层在本机启动 FastAPI（SQLite 位于用户数据目录），加载 Vue 前端。**酒店生产数据**来自设置中的 OTA 官方契约路径、PMS API 与渠道账号，而非长期依赖仓库演示种子。

仓库：**https://github.com/fenglan2010/Orio-Desktop**

## 酒店用户

**安装 → 打开 → 输入安装密钥 → 开通本店（酒店档案、管理员、房型）→ 登录工作台**。不会预装演示酒店或 `admin` / `admin123`。无需本机 Python、conda 或 `.venv`。

## IT 构建（PowerShell）

```powershell
git clone https://github.com/fenglan2010/Orio-Desktop.git Orio-Desktop
cd Orio-Desktop
# Node 20 LTS 推荐
.\desktop\scripts\build-windows.ps1 -WithInstaller
```

打包会嵌入 `resources\python\python.exe` + 后端依赖。酒店安装包不读取 PATH 上的 `python`/`py`。

完整说明：[docs/安装指南.md](../docs/安装指南.md) · [docs/操作运行指南.md](../docs/操作运行指南.md)

### 打包验收

```powershell
$root = "desktop\dist\win-unpacked"
Test-Path (Join-Path $root "resources\renderer\index.html")   # True
Test-Path (Join-Path $root "resources\backend\app\main.py")   # True
Test-Path (Join-Path $root "resources\python\python.exe")    # True
```

Electron 会等待 `/api/health` 与根路径 HTML 就绪后再显示窗口；后端失败时弹出中文错误页，不再静默白屏。

### 前端必须在 `resources\renderer`

`npm run pack:dir` / `pack:win` / `pack:win:nsis` 会先执行 `ensure-frontend-dist.js --build`（缺 `frontend\dist\index.html` 时自动构建，仍失败则 loud exit）。打包产物将前端放在 **`resources\renderer`**（`extraResources`），后端在 **`resources\backend`**。Python **不能**读 `app.asar` 内静态资源。

典型故障：**窗口空白** + `http://127.0.0.1:8765/` 返回 `{"detail":"Not Found"}` → 未正确打入 `resources\renderer`，需重新构建前端并打包。

### Python（正式包）

仅 `resources\python\python.exe`。开发模式仍可用仓库 `.venv` 或系统 Python。

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