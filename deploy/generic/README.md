# Generic Deployment

Deploy HiveGate using Docker Compose or Kubernetes on any infrastructure.

## Docker Compose (Production)

### Quick Start

1. **Create environment file**:

```bash
cp .env.example .env
# Edit .env with your production values
```

2. **Deploy**:

```bash
# Basic deployment (app + PostgreSQL)
docker compose -f deploy/generic/docker-compose.prod.yaml up -d

# With Qdrant vector database
docker compose -f deploy/generic/docker-compose.prod.yaml --profile with-qdrant up -d

# With Nginx reverse proxy
docker compose -f deploy/generic/docker-compose.prod.yaml --profile with-nginx up -d
```

3. **Verify**:

```bash
curl http://localhost:8080/health
```

### Environment Variables

Create a `.env` file:

```bash
# Database
DB_USER=agadmin
DB_PASS=secure-password-here
DB_DATABASE=hivegate

# Encryption
SECRET_TOKEN_ENC_KEY=your-base64-fernet-key

# Optional: Qdrant
QDRANT_URL=http://qdrant:6333
```

### SSL with Nginx

1. Place SSL certificates in `./ssl/`:
   - `ssl/cert.pem`
   - `ssl/key.pem`

2. Create `nginx.conf`:

```nginx
events {
    worker_connections 1024;
}

http {
    upstream backend {
        server hivegate:8080;
    }

    server {
        listen 80;
        return 301 https://$host$request_uri;
    }

    server {
        listen 443 ssl;
        ssl_certificate /etc/nginx/ssl/cert.pem;
        ssl_certificate_key /etc/nginx/ssl/key.pem;

        location / {
            proxy_pass http://backend;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
        }
    }
}
```

3. Start with Nginx profile:

```bash
docker compose -f deploy/generic/docker-compose.prod.yaml --profile with-nginx up -d
```

---

## Kubernetes

### Prerequisites

- Kubernetes cluster (1.24+)
- `kubectl` configured
- Ingress controller (nginx-ingress recommended)
- cert-manager (optional, for automatic SSL)

### Quick Start

1. **Create namespace**:

```bash
kubectl create namespace hivegate
```

2. **Update secrets**:

Edit `deploy/generic/kubernetes/configmap.yaml` and replace placeholder values:
- `CHANGE_ME_IN_PRODUCTION` - Database password
- `CHANGE_ME_BASE64_FERNET_KEY` - Encryption key

3. **Apply manifests**:

```bash
kubectl apply -f deploy/generic/kubernetes/ -n hivegate
```

4. **Verify**:

```bash
kubectl get pods -n hivegate
kubectl get svc -n hivegate
```

### Configuration

#### Update ConfigMap

```bash
kubectl create configmap hivegate-config \
  --from-literal=db-host=your-db-host \
  --from-literal=db-port=5432 \
  --from-literal=db-database=hivegate \
  -n hivegate \
  --dry-run=client -o yaml | kubectl apply -f -
```

#### Update Secrets

```bash
kubectl create secret generic hivegate-db \
  --from-literal=username=agadmin \
  --from-literal=password=your-secure-password \
  -n hivegate \
  --dry-run=client -o yaml | kubectl apply -f -

kubectl create secret generic hivegate-secrets \
  --from-literal=token-encryption-key=your-fernet-key \
  -n hivegate \
  --dry-run=client -o yaml | kubectl apply -f -
```

### Scaling

The deployment includes a HorizontalPodAutoscaler that scales between 2-10 replicas based on CPU/memory usage.

Manual scaling:

```bash
kubectl scale deployment hivegate --replicas=5 -n hivegate
```

### Ingress

Update `deploy/generic/kubernetes/service.yaml` with your domain:

```yaml
spec:
  tls:
    - hosts:
        - your-domain.com
      secretName: hivegate-tls
  rules:
    - host: your-domain.com
```

### Monitoring

```bash
# View logs
kubectl logs -f deployment/hivegate -n hivegate

# View events
kubectl get events -n hivegate --sort-by='.lastTimestamp'

# Port forward for local testing
kubectl port-forward svc/hivegate 8080:80 -n hivegate
```

---

## Helm Chart (Coming Soon)

A Helm chart will be available for easier configuration management:

```bash
helm repo add agno https://charts.agno.com
helm install hivegate hivegate/hivegate \
  --set database.host=your-db-host \
  --set secrets.tokenEncryptionKey=your-key
```

---

## Database Options

### Managed PostgreSQL Services

- **AWS RDS for PostgreSQL**
- **Google Cloud SQL**
- **Azure Database for PostgreSQL**
- **DigitalOcean Managed Databases**
- **Heroku Postgres**

### Self-Hosted PostgreSQL

For production self-hosted PostgreSQL:

1. Use PostgreSQL 15+
2. Enable SSL connections
3. Set up regular backups
4. Configure connection pooling (PgBouncer)

Example connection with SSL:

```bash
DB_HOST=your-postgres-host
DB_PORT=5432
DB_USER=agadmin
DB_PASS=secure-password
DB_DATABASE=hivegate
DB_SSL_MODE=require
```

---

## Troubleshooting

### Container won't start

```bash
# Check logs
docker logs hivegate

# Check health
docker inspect hivegate | jq '.[0].State.Health'
```

### Database connection issues

```bash
# Test database connectivity
docker exec hivegate python -c "
from db.session import get_db
next(get_db())
print('Database connection successful')
"
```

### Kubernetes pod crashes

```bash
# Describe pod
kubectl describe pod -l app=hivegate -n hivegate

# Check previous logs
kubectl logs -l app=hivegate -n hivegate --previous
```
