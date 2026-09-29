# ECS deployment

The AWS deployment of AI Receptionist, as code. Everything here is an
identifier or a setting; **no secret values live in this directory**. Credentials
stay in Secrets Manager (`ai-receptionist/production`) and are referenced by key
name.

```
Internet ──HTTPS──> CloudFront (d3e8unss81w8wv.cloudfront.net)
                        │ HTTP, CloudFront origin-facing IPs only
                        v
                   ALB ai-receptionist-alb
         /api/*, /healthz, /readyz │ everything else
                  v                v
          API service :8000   frontend service :3000
                  │
   worker ── RDS PostgreSQL (ai-receptionist-staging) ── ElastiCache Serverless Redis
   temporal-worker (0 tasks until Temporal Cloud credentials exist)
```

Region `us-east-2`, ECS cluster `ai-receptionist`, Fargate, the default VPC. Its
subnets are public and there is no NAT gateway, so tasks get a public IP for
egress. The task security group admits nothing from the internet: only the ALB
reaches ports 8000/3000, and only CloudFront reaches the ALB.

## Files

| File | What it is |
|---|---|
| `deploy.env` | Account, network, ARNs, service names, public URL, app mode. Bash syntax. |
| `deploy.local.env` | *Gitignored.* Personal overrides, sourced after `deploy.env` (e.g. `EMAIL_FROM`). |
| `backend.env` | Non-secret environment shared by every backend container. `${NAME}` comes from `deploy.env`. |
| `secrets.txt` | Secrets Manager **key names** injected into backend containers. |
| `taskdefs/*.json` | Task-definition templates: `api`, `worker`, `temporal-worker`, `migrate`, `frontend`. |
| `render.py` | Fills the templates into `.rendered/` (gitignored). Standard library only. |
| `deploy.sh` | Build → push → register → migrate → roll out → smoke test. |
| `cloudfront.json` | The CloudFront distribution config, for recreating it. |

## Deploying

```bash
infra/ecs/deploy.sh --plan          # read-only preview: preflight, render, print actions
infra/ecs/deploy.sh                 # deploy the current commit (tree must be clean)
infra/ecs/deploy.sh --tag e117750   # redeploy/roll back to a tag already in ECR
```

What `deploy.sh` does, in order, stopping at the first failure:

1. Checks the AWS account, and that every key in `secrets.txt` exists and is
   non-empty in the secret (it prints names only).
2. Builds and pushes `ai-receptionist:<sha>` (backend) and
   `ai-receptionist:frontend-<sha>` (frontend) unless the tags already exist.
   Tags are never rebuilt. The frontend bakes in `NEXT_PUBLIC_API_BASE_URL=$PUBLIC_URL`
   at build time, so changing `PUBLIC_URL` needs a new commit (new tag).
3. Renders and registers all five task definitions.
4. Runs `alembic upgrade head` as a one-off task and requires exit code 0. A
   failed migration leaves every service untouched.
5. Updates the four services to the new revisions. Each has the deployment
   circuit breaker with automatic rollback on.
6. Waits for steady state and smoke-tests `/healthz`, `/readyz` and `/`.

## Changing configuration

* **A non-secret setting**: edit `backend.env` or `deploy.env`, commit, deploy.
* **A secret**: change it in Secrets Manager. If it is a new key, also add its
  name to `secrets.txt`. Tasks read secrets at start, so redeploy (or
  `aws ecs update-service --force-new-deployment`) to pick it up.

### Enabling Temporal Cloud

1. Add `TEMPORAL_ADDRESS`, `TEMPORAL_NAMESPACE`, and either `TEMPORAL_API_KEY`
   or `TEMPORAL_TLS_CERT` + `TEMPORAL_TLS_KEY` (PEM text) to the secret.
2. List those names in `secrets.txt`.
3. In `deploy.env`: `ORCHESTRATOR=temporal`, `TEMPORAL_WORKER_COUNT=1`.
4. Deploy. Exactly one orchestrator runs at a time: under `temporal` the polling
   worker stops claiming provisioning runs by itself.

### Going production-like

`ELEVENLABS_WEBHOOK_SECRET` is in the secret and in `secrets.txt`; set
`APP_ENVIRONMENT=production`. The app refuses to boot a production-like
environment with unsafe settings; see `Settings._production_hardening`.
`DRY_RUN=false` comes last, after vendor webhooks are configured and
`EMAIL_FROM` is on a domain verified with Resend.

## Verifying

```bash
export AWS_REGION=us-east-2
aws ecs describe-services --cluster ai-receptionist \
  --services ai-receptionist-service-d78yjjez ai-receptionist-worker ai-receptionist-frontend ai-receptionist-temporal-worker \
  --query 'services[].[serviceName,desiredCount,runningCount,deployments[0].rolloutState]' --output table
curl -s https://d3e8unss81w8wv.cloudfront.net/healthz
curl -s https://d3e8unss81w8wv.cloudfront.net/readyz        # database + redis checks
aws logs tail /ecs/ai-receptionist --follow --log-stream-name-prefix api/
aws logs filter-log-events --log-group-name /ecs/ai-receptionist \
  --start-time $(( ($(date +%s) - 3600) * 1000 )) --filter-pattern '?ERROR ?Traceback' \
  --query 'events[].message' --output text
```

## One-time bootstrap (already done)

These resources exist and `deploy.sh` only updates them. The commands below
are what created them, kept so the environment can be rebuilt. Values come from
`deploy.env`.

**Pre-existing, reused:** the ECR repository, ECS cluster, RDS instance and its
security group (which already admits `TASK_SECURITY_GROUP` on 5432), the
ElastiCache Serverless cache, the secret, `ecsTaskExecutionRole` (with
`ECSSecretsManagerAccess`), and the API service `ai-receptionist-service-d78yjjez`.

**Created for this deployment:**

```bash
set -a; . infra/ecs/deploy.env; set +a; export MSYS_NO_PATHCONV=1
NETWORK="awsvpcConfiguration={subnets=[$SUBNETS],securityGroups=[$TASK_SECURITY_GROUP],assignPublicIp=$ASSIGN_PUBLIC_IP}"

# ALB security group: HTTP from CloudFront's origin-facing ranges only.
PL=$(aws ec2 describe-managed-prefix-lists --filters Name=prefix-list-name,Values=com.amazonaws.global.cloudfront.origin-facing --query 'PrefixLists[0].PrefixListId' --output text)
ALB_SG=$(aws ec2 create-security-group --group-name ai-receptionist-alb-sg --description "ALB for ai-receptionist; HTTP from CloudFront only" --vpc-id $VPC_ID --query GroupId --output text)
aws ec2 authorize-security-group-ingress --group-id $ALB_SG --ip-permissions "IpProtocol=tcp,FromPort=80,ToPort=80,PrefixListIds=[{PrefixListId=$PL}]"
for P in 8000 3000; do
  aws ec2 authorize-security-group-ingress --group-id $TASK_SECURITY_GROUP --ip-permissions "IpProtocol=tcp,FromPort=$P,ToPort=$P,UserIdGroupPairs=[{GroupId=$ALB_SG}]"
done

# ALB, target groups, listener, path rule.
ALB=$(aws elbv2 create-load-balancer --name ai-receptionist-alb --type application --scheme internet-facing --subnets ${SUBNETS//,/ } --security-groups $ALB_SG --query 'LoadBalancers[0].LoadBalancerArn' --output text)
aws elbv2 modify-load-balancer-attributes --load-balancer-arn $ALB --attributes Key=idle_timeout.timeout_seconds,Value=60 Key=routing.http.drop_invalid_header_fields.enabled,Value=true
API_TG=$(aws elbv2 create-target-group --name ai-receptionist-api-tg --protocol HTTP --port 8000 --vpc-id $VPC_ID --target-type ip --health-check-path /readyz --health-check-interval-seconds 15 --healthy-threshold-count 2 --unhealthy-threshold-count 3 --matcher HttpCode=200 --query 'TargetGroups[0].TargetGroupArn' --output text)
WEB_TG=$(aws elbv2 create-target-group --name ai-receptionist-web-tg --protocol HTTP --port 3000 --vpc-id $VPC_ID --target-type ip --health-check-path / --health-check-interval-seconds 15 --healthy-threshold-count 2 --unhealthy-threshold-count 3 --matcher HttpCode=200-399 --query 'TargetGroups[0].TargetGroupArn' --output text)
for TG in $API_TG $WEB_TG; do aws elbv2 modify-target-group-attributes --target-group-arn $TG --attributes Key=deregistration_delay.timeout_seconds,Value=30; done
L=$(aws elbv2 create-listener --load-balancer-arn $ALB --protocol HTTP --port 80 --default-actions Type=forward,TargetGroupArn=$WEB_TG --query 'Listeners[0].ListenerArn' --output text)
aws elbv2 create-rule --listener-arn $L --priority 10 --conditions '[{"Field":"path-pattern","PathPatternConfig":{"Values":["/api/*","/healthz","/readyz"]}}]' --actions Type=forward,TargetGroupArn=$API_TG

# CloudFront (edit CallerReference and the origin DomainName in cloudfront.json first
# when recreating). Caching is off except /_next/static/*; all headers, cookies and
# query strings reach the origin.
aws cloudfront create-distribution --distribution-config file://infra/ecs/cloudfront.json

# Task definitions must be registered first: run deploy.sh once (its service
# step fails on the missing services), then create the services and deploy again.
DC='deploymentCircuitBreaker={enable=true,rollback=true},maximumPercent=200,minimumHealthyPercent=100'
CPS='capacityProvider=FARGATE,weight=1'
aws ecs create-service --cluster $ECS_CLUSTER --service-name $WORKER_SERVICE --task-definition ai-receptionist-worker --desired-count 1 --capacity-provider-strategy $CPS --network-configuration "$NETWORK" --deployment-configuration "$DC" --propagate-tags SERVICE
aws ecs create-service --cluster $ECS_CLUSTER --service-name $TEMPORAL_WORKER_SERVICE --task-definition ai-receptionist-temporal-worker --desired-count $TEMPORAL_WORKER_COUNT --capacity-provider-strategy $CPS --network-configuration "$NETWORK" --deployment-configuration "$DC" --propagate-tags SERVICE
aws ecs create-service --cluster $ECS_CLUSTER --service-name $FRONTEND_SERVICE --task-definition ai-receptionist-frontend --desired-count 1 --capacity-provider-strategy $CPS --network-configuration "$NETWORK" --deployment-configuration "$DC" --propagate-tags SERVICE --load-balancers "targetGroupArn=$WEB_TARGET_GROUP_ARN,containerName=frontend,containerPort=3000" --health-check-grace-period-seconds 30
# The existing API service was attached to the ALB with:
aws ecs update-service --cluster $ECS_CLUSTER --service $API_SERVICE --load-balancers "targetGroupArn=$API_TARGET_GROUP_ARN,containerName=api,containerPort=8000" --health-check-grace-period-seconds 60 --deployment-configuration "$DC"
```

## Known gaps

* CloudFront → ALB is plain HTTP. A custom domain with an ACM certificate on the
  ALB closes that hop.
* The task role is the execution role; the app calls no AWS APIs, so it needs
  nothing more. Give it its own least-privilege role before it does.
* `INTEGRATION_ENCRYPTION_KEY` in the secret is not a valid Fernet key, so
  calendar integrations report "configuration required" until it is replaced.
