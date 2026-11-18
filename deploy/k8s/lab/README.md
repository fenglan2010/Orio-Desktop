# Lab-only in-cluster Postgres and Redis

**Not for production.** These manifests exist so a disposable cluster can
stand up a complete stack without managed databases. They are ClusterIP
only and must not be changed to NodePort, LoadBalancer, or a public
address.

Production should use 腾讯云 TencentDB / 阿里云 RDS PostgreSQL and managed
Redis in a VPC, with `sslmode=require` (or verify-ca / verify-full).

Apply separately after filling the lab secret placeholders:

```bash
# Do not commit the filled secret.
kubectl apply -k deploy/k8s/lab
```

Create matching `DATABASE_URL` and broker URLs in `oiro-secrets` that point
at `oiro-lab-postgres:5432` and `oiro-lab-redis:6379` inside the namespace.
That in-cluster Postgres has no TLS.
