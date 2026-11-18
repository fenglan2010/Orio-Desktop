# OIRO 部署清单

可复制部署（Docker Compose 与 Kubernetes）的文件都在本目录和仓库根的 `docker-compose.yml`。完整说明见 [docs/09-第三阶段.md](../docs/09-第三阶段.md) 的第四增量。

本清单不会改动本地 SQLite 演示（`scripts/dev.sh`，127.0.0.1:5173 / 8000）。不要把共享演示进程切到 Compose 里的 Postgres。

## Compose（本机或单机虚机）

```bash
cp deploy/compose.env.example deploy/compose.env
# 填写 JWT_SECRET 与 POSTGRES_PASSWORD（不要用演示密钥 oiro-demo-secret-change-me）
docker compose --env-file deploy/compose.env up -d --build
```

- 网页入口：http://127.0.0.1:8080
- API（仅本机）：http://127.0.0.1:18000/api/health
- Postgres 与 Redis 只在内部网络，不映射到主机端口。

## Kubernetes（厂商中立）

```bash
# 先根据 secret.yaml.example 创建 oiro-secrets，再应用清单
kubectl apply -k deploy/k8s/base
# 或使用腾讯云 TKE / 阿里云 ACK overlay（先改镜像仓库占位符）
kubectl apply -k deploy/k8s/overlays/tke
kubectl apply -k deploy/k8s/overlays/ack
```

生产环境优先把数据库和 Redis 接到同一 VPC 内的托管服务，而不是集群内的 lab 清单。`deploy/k8s/lab/` 仅供临时实验，不是生产源。
