# Azure Deployment

Deploy HiveGate to Azure Container Apps.

## Prerequisites

- Azure CLI installed and logged in
- Azure subscription with Container Apps enabled
- Docker for building images (or use Azure Container Registry build)

## Quick Start

### 1. Create Resource Group

```bash
az group create \
  --name hivegate-rg \
  --location eastus
```

### 2. Create Azure Container Registry (Optional)

```bash
az acr create \
  --resource-group hivegate-rg \
  --name hivegateacr \
  --sku Basic

# Build and push image
az acr build \
  --registry hivegateacr \
  --image hivegate:latest \
  --file Dockerfile .
```

### 3. Deploy with Bicep

```bash
# Deploy infrastructure and application
az deployment group create \
  --resource-group hivegate-rg \
  --template-file deploy/azure/container-apps/main.bicep \
  --parameters \
    environment=dev \
    containerImage=hivegateacr.azurecr.io/hivegate:latest \
    dbConnectionString="postgresql://user:pass@host:5432/db" \
    tokenEncryptionKey="your-base64-fernet-key"
```

### 4. Get Application URL

```bash
az containerapp show \
  --name hivegate-dev \
  --resource-group hivegate-rg \
  --query properties.configuration.ingress.fqdn \
  --output tsv
```

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                        Internet                              │
└─────────────────────────┬───────────────────────────────────┘
                          │
┌─────────────────────────▼───────────────────────────────────┐
│              Azure Container Apps                            │
│         (Built-in Ingress + Auto-scaling)                   │
│                                                              │
│  ┌─────────────────┐    ┌─────────────────┐                 │
│  │    Revision     │    │    Revision     │                 │
│  │  hivegate       │    │  hivegate       │                 │
│  └────────┬────────┘    └────────┬────────┘                 │
└───────────┼──────────────────────┼──────────────────────────┘
            │                      │
┌───────────▼──────────────────────▼──────────────────────────┐
│              Azure Database for PostgreSQL                   │
│                     (Flexible Server)                        │
└─────────────────────────────────────────────────────────────┘
```

## Configuration

### Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `DATABASE_URL` | Yes | PostgreSQL connection string |
| `SECRET_TOKEN_ENC_KEY` | Yes | Fernet encryption key |
| `QDRANT_URL` | No | Qdrant vector database URL |

### Scaling Configuration

Modify in Bicep template:

```bicep
scale: {
  minReplicas: 2        // Minimum instances
  maxReplicas: 20       // Maximum instances
  rules: [
    {
      name: 'http-scaling'
      http: {
        metadata: {
          concurrentRequests: '50'  // Scale up when requests > 50
        }
      }
    }
  ]
}
```

## Database Setup

### Create Azure Database for PostgreSQL

```bash
# Create PostgreSQL Flexible Server
az postgres flexible-server create \
  --resource-group hivegate-rg \
  --name hivegate-db \
  --location eastus \
  --admin-user agadmin \
  --admin-password 'YourSecurePassword123!' \
  --sku-name Standard_B1ms \
  --tier Burstable \
  --version 15

# Create database
az postgres flexible-server db create \
  --resource-group hivegate-rg \
  --server-name hivegate-db \
  --database-name hivegate

# Allow Azure services
az postgres flexible-server firewall-rule create \
  --resource-group hivegate-rg \
  --name hivegate-db \
  --rule-name AllowAzureServices \
  --start-ip-address 0.0.0.0 \
  --end-ip-address 0.0.0.0
```

## Costs

Estimated monthly costs (East US):

- **Container Apps (0.5 vCPU, 1GB, 2 replicas)**: ~$30
- **PostgreSQL (Burstable B1ms)**: ~$25
- **Log Analytics**: ~$5-10

Total: ~$60-70/month for development workloads.

## Monitoring

### View Logs

```bash
az containerapp logs show \
  --name hivegate-dev \
  --resource-group hivegate-rg \
  --follow
```

### View Metrics

```bash
az monitor metrics list \
  --resource /subscriptions/{sub}/resourceGroups/hivegate-rg/providers/Microsoft.App/containerApps/hivegate-dev \
  --metric "Requests" \
  --interval PT1H
```

## Troubleshooting

### Check revision status

```bash
az containerapp revision list \
  --name hivegate-dev \
  --resource-group hivegate-rg \
  --output table
```

### Restart application

```bash
az containerapp revision restart \
  --name hivegate-dev \
  --resource-group hivegate-rg \
  --revision <revision-name>
```

### Update container image

```bash
az containerapp update \
  --name hivegate-dev \
  --resource-group hivegate-rg \
  --image hivegateacr.azurecr.io/hivegate:v2.0.0
```
