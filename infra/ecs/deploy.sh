#!/usr/bin/env bash
# =============================================================================
# Deploy AI Receptionist to ECS.
#
#   infra/ecs/deploy.sh                 # build, push, migrate, roll out HEAD
#   infra/ecs/deploy.sh --plan          # read-only: preflight + render, print the rest
#   infra/ecs/deploy.sh --tag abc1234   # deploy an already-pushed tag (no build)
#
# Order matters and is enforced: images exist -> task definitions registered ->
# migrations succeed -> services updated. A failed migration stops the rollout
# before any running service changes.
#
# Needs: aws CLI v2, docker (unless the tag is already in ECR), git, python 3.
# Secret values are never read into the shell or printed; only key names are.
# =============================================================================
set -euo pipefail

# Native paths (`pwd -W` on Git Bash gives C:/...), because MSYS_NO_PATHCONV
# below also stops /c/... paths being translated for python and the aws CLI.
native_pwd() { pwd -W 2>/dev/null || pwd; }
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && native_pwd)"
ROOT="$(cd "$HERE/../.." && native_pwd)"
# Git Bash on Windows would otherwise rewrite /ecs/... into a Windows path.
export MSYS_NO_PATHCONV=1

PLAN=0
TAG=""
while [ $# -gt 0 ]; do
  case "$1" in
    --plan) PLAN=1 ;;
    --tag) TAG="${2:?--tag needs a value}"; shift ;;
    -h|--help) sed -n '2,16p' "$0"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
  shift
done

set -a
# shellcheck source=deploy.env
. "$HERE/deploy.env"
[ -f "$HERE/deploy.local.env" ] && . "$HERE/deploy.local.env"
set +a

# The first interpreter that actually runs: on Windows `python3` is often the
# Microsoft Store stub, which exists on PATH but only prints an install hint.
PY=""
for candidate in python3 python; do
  if "$candidate" -c 'import sys; sys.exit(sys.version_info < (3, 8))' >/dev/null 2>&1; then
    PY="$candidate"; break
  fi
done
[ -n "$PY" ] || { echo "error: python 3.8+ is required" >&2; exit 1; }
step() { printf '\n==> %s\n' "$*"; }
die() { echo "error: $*" >&2; exit 1; }
# Mutating commands go through here, so --plan prints instead of acting.
run() { if [ "$PLAN" = 1 ]; then echo "  [plan] $*"; else "$@"; fi; }

if [ -z "$TAG" ]; then
  if [ -n "$(git -C "$ROOT" status --porcelain)" ] && [ "$PLAN" = 0 ]; then
    die "working tree has uncommitted changes; the image tag would not describe what was built"
  fi
  TAG="$(git -C "$ROOT" rev-parse --short HEAD)"
fi
REGISTRY="$AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com"
export IMAGE_TAG="$TAG"
export BACKEND_IMAGE="$REGISTRY/$ECR_REPOSITORY:$TAG"
export FRONTEND_IMAGE="$REGISTRY/$ECR_REPOSITORY:frontend-$TAG"
NETWORK="awsvpcConfiguration={subnets=[$SUBNETS],securityGroups=[$TASK_SECURITY_GROUP],assignPublicIp=$ASSIGN_PUBLIC_IP}"

# ---- 1. Preflight -----------------------------------------------------------
step "preflight (tag $TAG, environment $APP_ENVIRONMENT, dry_run $DRY_RUN)"
account="$(aws sts get-caller-identity --query Account --output text)"
[ "$account" = "$AWS_ACCOUNT_ID" ] || die "credentials are for account $account, not $AWS_ACCOUNT_ID"

# ECS will not start a task that references a missing secret key, and the
# failure surfaces minutes later as a task that never runs. Check names now.
# The secret is parsed inside python and only missing *names* are printed.
names="$("$PY" "$HERE/render.py" --secret-names | tr -d '\r' | tr '\n' ' ')"
[ -n "${names// /}" ] || die "no secret names read from secrets.txt"
missing="$(aws secretsmanager get-secret-value --secret-id "$SECRET_ARN" --query SecretString --output text \
  | "$PY" -c 'import json,sys; have=json.load(sys.stdin); print(" ".join(k for k in sys.argv[1:] if not str(have.get(k,"")).strip()))' \
    $names)"
[ -z "$missing" ] || die "secret is missing or empty for: $missing"
echo "all $(echo $names | wc -w | tr -d ' ') secret keys present"

# ---- 2. Images --------------------------------------------------------------
image_exists() {
  aws ecr describe-images --repository-name "$ECR_REPOSITORY" --image-ids "imageTag=$1" >/dev/null 2>&1
}
step "images"
# Tags are treated as immutable: an existing tag is reused, never rebuilt.
need_build=0
image_exists "$TAG" || need_build=1
image_exists "frontend-$TAG" || need_build=1
if [ "$need_build" = 1 ]; then
  [ "$(git -C "$ROOT" rev-parse --short HEAD)" = "$TAG" ] \
    || die "tag $TAG is not in ECR and is not HEAD; check it out to build it"
  if [ "$PLAN" = 0 ]; then
    aws ecr get-login-password | docker login --username AWS --password-stdin "$REGISTRY" >/dev/null
  fi
  if ! image_exists "$TAG"; then
    run docker build --platform linux/amd64 --target runtime -t "$BACKEND_IMAGE" "$ROOT/backend"
    run docker push "$BACKEND_IMAGE"
  fi
  if ! image_exists "frontend-$TAG"; then
    # NEXT_PUBLIC_* is compiled into the browser bundle, so the frontend image
    # is specific to the URL it will be served from.
    run docker build --platform linux/amd64 --target runtime \
      --build-arg "NEXT_PUBLIC_API_BASE_URL=$PUBLIC_URL" -t "$FRONTEND_IMAGE" "$ROOT/frontend"
    run docker push "$FRONTEND_IMAGE"
  fi
else
  echo "$BACKEND_IMAGE and frontend-$TAG already in ECR"
fi

# ---- 3. Task definitions ----------------------------------------------------
step "task definitions"
RENDERED="$HERE/.rendered"
"$PY" "$HERE/render.py" "$RENDERED"
declare -A TD
for name in api migrate worker temporal-worker frontend; do
  if [ "$PLAN" = 1 ]; then
    TD[$name]="<new revision of $name>"
    echo "  [plan] aws ecs register-task-definition --cli-input-json file://$RENDERED/$name.json"
  else
    TD[$name]="$(aws ecs register-task-definition --cli-input-json "file://$RENDERED/$name.json" \
      --query taskDefinition.taskDefinitionArn --output text)"
    echo "  ${TD[$name]##*/}"
  fi
done

# ---- 4. Migrations ----------------------------------------------------------
step "migrations (alembic upgrade head, one-off task)"
if [ "$PLAN" = 1 ]; then
  echo "  [plan] aws ecs run-task --task-definition ${TD[migrate]} ... then wait and require exit code 0"
else
  task="$(aws ecs run-task --cluster "$ECS_CLUSTER" --task-definition "${TD[migrate]}" \
    --capacity-provider-strategy capacityProvider=FARGATE,weight=1 \
    --network-configuration "$NETWORK" --started-by "deploy-$TAG" \
    --query 'tasks[0].taskArn' --output text)"
  aws ecs wait tasks-stopped --cluster "$ECS_CLUSTER" --tasks "$task"
  code="$(aws ecs describe-tasks --cluster "$ECS_CLUSTER" --tasks "$task" \
    --query 'tasks[0].containers[0].exitCode' --output text)"
  aws logs get-log-events --log-group-name "$LOG_GROUP" --log-stream-name "migrate/migrate/${task##*/}" \
    --start-from-head --query 'events[].[message]' --output text 2>/dev/null | tail -20 || true
  [ "$code" = "0" ] || die "migration task ${task##*/} exited with $code; services were not updated"
  echo "  migrations applied"
fi

# ---- 5. Services ------------------------------------------------------------
step "services"
update() { # service task-definition [extra args...]
  local service="$1" td="$2"; shift 2
  aws ecs describe-services --cluster "$ECS_CLUSTER" --services "$service" \
    --query 'services[?status==`ACTIVE`].serviceName' --output text | grep -q . \
    || die "service $service does not exist; create it once as described in infra/ecs/README.md"
  run aws ecs update-service --cluster "$ECS_CLUSTER" --service "$service" --task-definition "$td" "$@" \
    --query 'service.[serviceName,taskDefinition]' --output text
}
update "$API_SERVICE" "${TD[api]}"
update "$WORKER_SERVICE" "${TD[worker]}"
update "$FRONTEND_SERVICE" "${TD[frontend]}"
update "$TEMPORAL_WORKER_SERVICE" "${TD[temporal-worker]}" --desired-count "$TEMPORAL_WORKER_COUNT"

if [ "$PLAN" = 1 ]; then
  step "plan complete; nothing was changed"
  exit 0
fi

# ---- 6. Verify --------------------------------------------------------------
step "waiting for services to stabilise"
aws ecs wait services-stable --cluster "$ECS_CLUSTER" \
  --services "$API_SERVICE" "$WORKER_SERVICE" "$FRONTEND_SERVICE" "$TEMPORAL_WORKER_SERVICE"
aws ecs describe-services --cluster "$ECS_CLUSTER" \
  --services "$API_SERVICE" "$WORKER_SERVICE" "$FRONTEND_SERVICE" "$TEMPORAL_WORKER_SERVICE" \
  --query 'services[].[serviceName,desiredCount,runningCount,deployments[0].rolloutState]' --output table

step "smoke test $PUBLIC_URL"
curl -fsS "$PUBLIC_URL/healthz" && echo
curl -fsS "$PUBLIC_URL/readyz" && echo
curl -fsS -o /dev/null -w "frontend / -> HTTP %{http_code}\n" "$PUBLIC_URL/"
step "deployed $TAG"
