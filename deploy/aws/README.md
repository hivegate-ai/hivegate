# AWS Deployment

Deploy HiveGate to AWS using ECS Fargate.

## Prerequisites

- AWS CLI configured with appropriate credentials
- Docker installed for building images
- AWS account with permissions for ECS, ECR, VPC, ALB, IAM, Secrets Manager

## Quick Start

### 1. Create Secrets

Store your database credentials and encryption key in AWS Secrets Manager:

```bash
# Create database secret
aws secretsmanager create-secret \
  --name hivegate/db \
  --secret-string '{
    "username": "your-db-user",
    "password": "your-db-password",
    "host": "your-db-host",
    "port": "5432",
    "database": "hivegate"
  }'

# Create encryption key secret
aws secretsmanager create-secret \
  --name hivegate/encryption-key \
  --secret-string "your-base64-fernet-key"
```

### 2. Build and Push Docker Image

```bash
# Login to ECR
aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin ${AWS_ACCOUNT_ID}.dkr.ecr.us-east-1.amazonaws.com

# Build and push
docker build -t hivegate .
docker tag hivegate:latest ${AWS_ACCOUNT_ID}.dkr.ecr.us-east-1.amazonaws.com/hivegate:latest
docker push ${AWS_ACCOUNT_ID}.dkr.ecr.us-east-1.amazonaws.com/hivegate:latest
```

### 3. Deploy with CloudFormation

```bash
aws cloudformation create-stack \
  --stack-name hivegate-dev \
  --template-body file://deploy/aws/cloudformation.yaml \
  --parameters \
    ParameterKey=Environment,ParameterValue=dev \
    ParameterKey=VpcId,ParameterValue=vpc-xxxxx \
    ParameterKey=SubnetIds,ParameterValue="subnet-xxxxx,subnet-yyyyy" \
  --capabilities CAPABILITY_NAMED_IAM
```

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                        Internet                              │
└─────────────────────────┬───────────────────────────────────┘
                          │
┌─────────────────────────▼───────────────────────────────────┐
│              Application Load Balancer                       │
│                   (HTTP/HTTPS)                               │
└─────────────────────────┬───────────────────────────────────┘
                          │
┌─────────────────────────▼───────────────────────────────────┐
│                    ECS Cluster                               │
│  ┌─────────────────┐    ┌─────────────────┐                 │
│  │  Fargate Task   │    │  Fargate Task   │                 │
│  │  hivegate       │    │  hivegate       │                 │
│  └────────┬────────┘    └────────┬────────┘                 │
└───────────┼──────────────────────┼──────────────────────────┘
            │                      │
┌───────────▼──────────────────────▼──────────────────────────┐
│                    Amazon RDS                                │
│                   (PostgreSQL)                               │
└─────────────────────────────────────────────────────────────┘
```

## Configuration

### Environment Variables

Set in task definition or Secrets Manager:

| Variable | Required | Description |
|----------|----------|-------------|
| `DB_USER` | Yes | Database username |
| `DB_PASS` | Yes | Database password |
| `DB_HOST` | Yes | Database host |
| `DB_PORT` | Yes | Database port (default: 5432) |
| `DB_DATABASE` | Yes | Database name |
| `SECRET_TOKEN_ENC_KEY` | Yes | Fernet encryption key |
| `QDRANT_URL` | No | Qdrant vector database URL |

### Scaling

The default configuration uses 2 tasks. Adjust in CloudFormation:

```yaml
ECSService:
  Properties:
    DesiredCount: 4  # Increase for more capacity
```

For auto-scaling, add:

```yaml
ScalableTarget:
  Type: AWS::ApplicationAutoScaling::ScalableTarget
  Properties:
    MaxCapacity: 10
    MinCapacity: 2
    ResourceId: !Sub 'service/${ECSCluster}/${ECSService.Name}'
    ScalableDimension: ecs:service:DesiredCount
    ServiceNamespace: ecs

ScalingPolicy:
  Type: AWS::ApplicationAutoScaling::ScalingPolicy
  Properties:
    PolicyName: cpu-scaling
    PolicyType: TargetTrackingScaling
    ScalingTargetId: !Ref ScalableTarget
    TargetTrackingScalingPolicyConfiguration:
      TargetValue: 70
      PredefinedMetricSpecification:
        PredefinedMetricType: ECSServiceAverageCPUUtilization
```

## Costs

Estimated monthly costs (us-east-1):

- **Fargate (2 tasks, 0.5 vCPU, 1GB)**: ~$30
- **Application Load Balancer**: ~$20
- **RDS (db.t3.micro)**: ~$15
- **Data Transfer**: Variable

Total: ~$65-100/month for development workloads.

## Troubleshooting

### View logs

```bash
aws logs tail /ecs/hivegate-dev --follow
```

### Check service status

```bash
aws ecs describe-services \
  --cluster hivegate-dev \
  --services hivegate-dev
```

### Force new deployment

```bash
aws ecs update-service \
  --cluster hivegate-dev \
  --service hivegate-dev \
  --force-new-deployment
```
