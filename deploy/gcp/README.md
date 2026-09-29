# Google Cloud Platform Deployment

Deploy HiveGate to Google Cloud Run.

## Prerequisites

- Google Cloud SDK (`gcloud`) installed and configured
- Docker installed for building images
- GCP project with Cloud Run, Container Registry, and Secret Manager APIs enabled

## Quick Start

### 1. Set Environment Variables

```bash
export PROJECT_ID="your-project-id"
export REGION="us-central1"
export SERVICE_NAME="hivegate"
```

### 2. Enable Required APIs

```bash
gcloud services enable \
  run.googleapis.com \
  containerregistry.googleapis.com \
  secretmanager.googleapis.com \
  sqladmin.googleapis.com \
  --project=$PROJECT_ID
```

### 3. Create Secrets

```bash
# Create database credentials secret
echo -n '{"username":"user","password":"pass","host":"host","database":"db"}' | \
  gcloud secrets create db-credentials \
    --data-file=- \
    --project=$PROJECT_ID

# Create encryption key secret
echo -n "your-base64-fernet-key" | \
  gcloud secrets create token-encryption-key \
    --data-file=- \
    --project=$PROJECT_ID
```

### 4. Build and Push Image

```bash
# Build using Cloud Build
gcloud builds submit \
  --tag gcr.io/$PROJECT_ID/hivegate:latest \
  --project=$PROJECT_ID

# Or build locally and push
docker build -t gcr.io/$PROJECT_ID/hivegate:latest .
docker push gcr.io/$PROJECT_ID/hivegate:latest
```

### 5. Deploy to Cloud Run

```bash
gcloud run deploy $SERVICE_NAME \
  --image gcr.io/$PROJECT_ID/hivegate:latest \
  --platform managed \
  --region $REGION \
  --allow-unauthenticated \
  --port 8080 \
  --cpu 1 \
  --memory 1Gi \
  --min-instances 1 \
  --max-instances 10 \
  --set-secrets="DB_USER=db-credentials:username,DB_PASS=db-credentials:password,DB_HOST=db-credentials:host,DB_DATABASE=db-credentials:database,SECRET_TOKEN_ENC_KEY=token-encryption-key:latest" \
  --project=$PROJECT_ID
```

### 6. Get Service URL

```bash
gcloud run services describe $SERVICE_NAME \
  --platform managed \
  --region $REGION \
  --format 'value(status.url)' \
  --project=$PROJECT_ID
```

## Using Existing Scripts

This repository includes deployment scripts in `scripts/`:

```bash
# Build and push to GCR
./scripts/build_and_push_to_gcp.sh

# Deploy to Cloud Run
./scripts/deploy_to_cloud_run.sh

# Or with a specific tag
./scripts/build_and_push_to_gcp.sh v1.0.0
./scripts/deploy_to_cloud_run.sh v1.0.0
```

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                        Internet                              │
└─────────────────────────┬───────────────────────────────────┘
                          │
┌─────────────────────────▼───────────────────────────────────┐
│                   Cloud Run                                  │
│           (Auto-scaling, HTTPS, Load Balancing)             │
│                                                              │
│  ┌─────────────────┐    ┌─────────────────┐                 │
│  │    Instance     │    │    Instance     │                 │
│  │  hivegate       │    │  hivegate       │                 │
│  └────────┬────────┘    └────────┬────────┘                 │
└───────────┼──────────────────────┼──────────────────────────┘
            │                      │
            └──────────┬───────────┘
                       │
┌──────────────────────▼──────────────────────────────────────┐
│                     Cloud SQL                                │
│                   (PostgreSQL)                               │
└─────────────────────────────────────────────────────────────┘
```

## Configuration

### Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `DB_USER` | Yes | Database username |
| `DB_PASS` | Yes | Database password |
| `DB_HOST` | Yes | Database host (Cloud SQL private IP) |
| `DB_PORT` | No | Database port (default: 5432) |
| `DB_DATABASE` | Yes | Database name |
| `SECRET_TOKEN_ENC_KEY` | Yes | Fernet encryption key |
| `QDRANT_URL` | No | Qdrant vector database URL |

### Scaling Configuration

```bash
gcloud run services update $SERVICE_NAME \
  --min-instances 2 \
  --max-instances 20 \
  --concurrency 100 \
  --region $REGION \
  --project=$PROJECT_ID
```

## Cloud SQL Setup

### Create Cloud SQL Instance

```bash
# Create PostgreSQL instance
gcloud sql instances create hivegate-db \
  --database-version=POSTGRES_15 \
  --tier=db-f1-micro \
  --region=$REGION \
  --root-password=your-root-password \
  --project=$PROJECT_ID

# Create database
gcloud sql databases create hivegate \
  --instance=hivegate-db \
  --project=$PROJECT_ID

# Create user
gcloud sql users create agadmin \
  --instance=hivegate-db \
  --password=your-user-password \
  --project=$PROJECT_ID
```

### Connect Cloud Run to Cloud SQL

```bash
gcloud run services update $SERVICE_NAME \
  --add-cloudsql-instances=$PROJECT_ID:$REGION:hivegate-db \
  --region $REGION \
  --project=$PROJECT_ID
```

## Costs

Estimated monthly costs (us-central1):

- **Cloud Run (1 vCPU, 1GB, 2 instances avg)**: ~$30
- **Cloud SQL (db-f1-micro)**: ~$10
- **Secret Manager**: ~$1
- **Container Registry**: ~$1-5

Total: ~$45-50/month for development workloads.

## Monitoring

### View Logs

```bash
gcloud logging read "resource.type=cloud_run_revision AND resource.labels.service_name=$SERVICE_NAME" \
  --limit 100 \
  --project=$PROJECT_ID
```

### View Metrics

Use Cloud Console or:

```bash
gcloud monitoring metrics list \
  --filter="metric.type=run.googleapis.com/request_count"
```

## Troubleshooting

### Check revision status

```bash
gcloud run revisions list \
  --service $SERVICE_NAME \
  --region $REGION \
  --project=$PROJECT_ID
```

### View container logs

```bash
gcloud run services logs read $SERVICE_NAME \
  --region $REGION \
  --project=$PROJECT_ID
```

### Force redeploy

```bash
gcloud run deploy $SERVICE_NAME \
  --image gcr.io/$PROJECT_ID/hivegate:latest \
  --region $REGION \
  --project=$PROJECT_ID
```
