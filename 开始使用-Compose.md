# 在本地用 Docker Compose 运行 OIRO

这是生产形态的完整栈（API、网页、PostgreSQL、Redis、Celery），和本机 `scripts/dev.sh` 的 SQLite 演示不是同一条路径。网页开在 [http://127.0.0.1:8080](http://127.0.0.1:8080)，不会占用 5173 / 8000。

## 1. 准备

- 安装并启动 [Docker Desktop](https://www.docker.com/products/docker-desktop/)，菜单里显示正在运行。
- 解压本包后，进入里面的 `oiro` 目录，确认能看到 `docker-compose.yml`。

在终端里检查：

```bash
docker version
docker compose version
```

两条都能输出版本即可。

## 2. 填写本地密钥

macOS / Linux / Git Bash：

```bash
cp deploy/compose.env.example deploy/compose.env
```

Windows PowerShell：

```powershell
Copy-Item deploy\compose.env.example deploy\compose.env
```

用编辑器打开 `deploy/compose.env`，改这两项：

- `JWT_SECRET`：换成一长串随机字符。**不要**使用 `oiro-demo-secret-change-me`，否则生产守卫会拒绝启动。可在终端生成：`openssl rand -hex 32`
- `POSTGRES_PASSWORD`：换成较强的密码，**只使用字母和数字**，不要用 `@`、`#`、`:`、`/`、`?` 等字符，以免拼进数据库地址时出错。

不要把填好的 `deploy/compose.env` 发给别人或提交到仓库。

## 3. 启动

在 `oiro` 目录执行：

```bash
docker compose --env-file deploy/compose.env up -d --build
```

第一次构建镜像通常需要几分钟，请等命令结束。然后在浏览器打开 [http://127.0.0.1:8080](http://127.0.0.1:8080)。

演示账号（空库启动时会自动写入）：

- 杭州西湖精品酒店（演示）：`admin` / `admin123`，还有 `manager` / `manager123`、`approver` / `approver123`、`analyst` / `analyst123`
- 成都春熙商务酒店（演示）：`admin_b` / `admin123`（只能看到成都店）

默认是沙盒写入、自动执行关闭、境外模型关闭，也不会安装生产官方契约。

可另开一页检查接口： [http://127.0.0.1:18000/api/health](http://127.0.0.1:18000/api/health)

## 4. 常用命令

都在 `oiro` 目录下执行：

```bash
# 看日志
docker compose --env-file deploy/compose.env logs -f

# 看容器是否都在跑
docker compose --env-file deploy/compose.env ps

# 停止（保留数据库数据）
docker compose --env-file deploy/compose.env down

# 停止并删除数据库/Redis 数据卷（下次启动会重新写入演示数据）
docker compose --env-file deploy/compose.env down -v
```

## 5. 遇到问题时

- `docker` 找不到，或提示 daemon 未运行：先打开 Docker Desktop，等它完全启动后再试。
- 8080 或 18000 端口被占用：在 `deploy/compose.env` 里改 `OIRO_WEB_PORT`、`OIRO_API_PORT`（去掉前面的 `#`），然后再 `up`。
- 容器反复重启：打开日志确认 `JWT_SECRET` 是不是还写成了演示值，以及 `POSTGRES_PASSWORD` 是否含特殊字符。
- 网页打不开但健康检查可以：确认访问的是 `http://127.0.0.1:8080`，不是 5173 或 8000。

更完整的部署说明见 `deploy/README.md` 和 `docs/09-第三阶段.md`。
