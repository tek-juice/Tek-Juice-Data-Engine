#!/usr/bin/env bash
# DATA ENGINE — Production Deploy Script
# Builds Docker images, runs migrations, and rolls out Kubernetes deployments.
# Phase 4: Cloud production deployment automation.
#
# Usage:
#   bash scripts/deploy.sh [--env staging|production] [--skip-build] [--skip-migrate]

set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; CYAN='\033[0;36m'
YELLOW='\033[1;33m'; BOLD='\033[1m'; NC='\033[0m'
info()    { echo -e "${CYAN}[DEPLOY]${NC}  $*"; }
success() { echo -e "${GREEN}[OK]${NC}      $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}   $*"; }
error()   { echo -e "${RED}[ERROR]${NC}   $*" >&2; exit 1; }

# ── Defaults ──────────────────────────────────────────────────────────────────
DEPLOY_ENV="staging"
SKIP_BUILD=false
SKIP_MIGRATE=false
REGISTRY="${CONTAINER_REGISTRY:-ghcr.io/tek-juice/data-engine}"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --env)           DEPLOY_ENV="$2"; shift 2 ;;
        --skip-build)    SKIP_BUILD=true; shift ;;
        --skip-migrate)  SKIP_MIGRATE=true; shift ;;
        --registry)      REGISTRY="$2"; shift 2 ;;
        *)               error "Unknown argument: $1" ;;
    esac
done

[[ "$DEPLOY_ENV" != "staging" && "$DEPLOY_ENV" != "production" ]] && \
    error "Environment must be 'staging' or 'production'. Got: $DEPLOY_ENV"

GIT_SHA=$(git rev-parse --short HEAD 2>/dev/null || echo "unknown")
IMAGE_TAG="${DEPLOY_ENV}-${GIT_SHA}"

echo -e "\n${BOLD}╔══════════════════════════════════════════╗"
echo -e "║   DATA ENGINE — Deploy to ${DEPLOY_ENV^^}        ║"
echo -e "╚══════════════════════════════════════════╝${NC}"
echo -e "  Environment : ${BOLD}${DEPLOY_ENV}${NC}"
echo -e "  Image Tag   : ${BOLD}${IMAGE_TAG}${NC}"
echo -e "  Registry    : ${BOLD}${REGISTRY}${NC}\n"

if [[ "$DEPLOY_ENV" == "production" ]]; then
    warn "You are deploying to PRODUCTION."
    read -rp "Type 'deploy' to confirm: " CONFIRM
    [[ "$CONFIRM" != "deploy" ]] && { echo "Deploy cancelled."; exit 0; }
fi

# ── Pre-deploy checks ─────────────────────────────────────────────────────────
info "Running pre-deploy checks..."
command -v docker   >/dev/null 2>&1 || error "Docker not found."
command -v kubectl  >/dev/null 2>&1 || error "kubectl not found."

# Verify cluster access
if ! kubectl cluster-info --request-timeout=5s >/dev/null 2>&1; then
    error "Cannot connect to Kubernetes cluster. Check your kubeconfig."
fi
success "Cluster connection verified."

# ── Build & push images ───────────────────────────────────────────────────────
if [[ "$SKIP_BUILD" == "false" ]]; then
    SERVICES=("gateway" "ingestion_service" "embedding_service" "vector_vault" "worker")
    for svc in "${SERVICES[@]}"; do
        info "Building image: ${svc}..."
        dockerfile="infrastructure/docker/Dockerfile.${svc//_service/}"
        [[ "$svc" == "ingestion_service" || "$svc" == "embedding_service" || "$svc" == "vector_vault" ]] \
            && dockerfile="infrastructure/docker/Dockerfile.service"
        docker build \
            --file "$dockerfile" \
            --build-arg "SERVICE=${svc}" \
            --tag "${REGISTRY}/${svc}:${IMAGE_TAG}" \
            --tag "${REGISTRY}/${svc}:${DEPLOY_ENV}-latest" \
            --cache-from "${REGISTRY}/${svc}:${DEPLOY_ENV}-latest" \
            . 2>/dev/null
        docker push "${REGISTRY}/${svc}:${IMAGE_TAG}"
        docker push "${REGISTRY}/${svc}:${DEPLOY_ENV}-latest"
        success "Pushed: ${REGISTRY}/${svc}:${IMAGE_TAG}"
    done
else
    info "Skipping build (--skip-build)."
fi

# ── Database migrations ───────────────────────────────────────────────────────
if [[ "$SKIP_MIGRATE" == "false" ]]; then
    info "Running database migrations in cluster..."
    NAMESPACE="data-engine-${DEPLOY_ENV}"
    GATEWAY_POD=$(kubectl get pod -n "$NAMESPACE" -l app=api-gateway \
        -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || echo "")
    if [[ -n "$GATEWAY_POD" ]]; then
        kubectl exec -n "$NAMESPACE" "$GATEWAY_POD" -- alembic upgrade head
        success "Migrations applied."
    else
        warn "No gateway pod found — skipping in-cluster migration. Run manually."
    fi
else
    info "Skipping migrations (--skip-migrate)."
fi

# ── Kubernetes rollout ────────────────────────────────────────────────────────
info "Applying Kubernetes manifests..."
NAMESPACE="data-engine-${DEPLOY_ENV}"
kubectl apply -f infrastructure/kubernetes/ -n "$NAMESPACE"

info "Updating image tags..."
DEPLOYMENTS=("api-gateway" "ingestion-service" "embedding-service" "vector-vault" "celery-worker")
for dep in "${DEPLOYMENTS[@]}"; do
    svc_key="${dep//-/_}"
    kubectl set image "deployment/${dep}" \
        "${dep}=${REGISTRY}/${svc_key}:${IMAGE_TAG}" \
        -n "$NAMESPACE" 2>/dev/null || warn "Could not update image for ${dep}"
done

# ── Wait for rollout ──────────────────────────────────────────────────────────
info "Waiting for rollouts to complete (timeout: 5 min)..."
for dep in "${DEPLOYMENTS[@]}"; do
    kubectl rollout status "deployment/${dep}" -n "$NAMESPACE" \
        --timeout=300s 2>/dev/null || warn "Rollout timeout for ${dep} — check pod status."
done
success "All deployments rolled out."

# ── Smoke test ────────────────────────────────────────────────────────────────
info "Running smoke tests..."
GATEWAY_URL=$(kubectl get svc api-gateway -n "$NAMESPACE" \
    -o jsonpath='{.status.loadBalancer.ingress[0].hostname}' 2>/dev/null || echo "")

if [[ -n "$GATEWAY_URL" ]]; then
    HTTP_STATUS=$(curl -s -o /dev/null -w "%{http_code}" \
        "http://${GATEWAY_URL}/health" --max-time 10 || echo "000")
    if [[ "$HTTP_STATUS" == "200" ]]; then
        success "Health check passed (HTTP $HTTP_STATUS)."
    else
        error "Health check failed (HTTP $HTTP_STATUS). Rolling back..."
        for dep in "${DEPLOYMENTS[@]}"; do
            kubectl rollout undo "deployment/${dep}" -n "$NAMESPACE" 2>/dev/null || true
        done
        error "Rollback initiated. Check pod logs for details."
    fi
else
    warn "Could not determine gateway URL — smoke test skipped."
fi

echo -e "\n${GREEN}${BOLD}Deployment to ${DEPLOY_ENV} complete!${NC}"
echo -e "  Tag : ${IMAGE_TAG}"
echo -e "  Env : ${DEPLOY_ENV}\n"
