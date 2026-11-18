# ADR-0010: Docker Compose locally; Kubernetes manifests provided but not required

- Status: Accepted

## Decision
Local and single-node environments use Docker Compose with profiles (`infra`, `app`, `observability`, `full`).
Reference Kubernetes manifests (Deployment with migration init-container, HPA, PDB, non-root/read-only containers)
live in `infrastructure/deployment/k8s/` for when multi-node scaling is required. No service mesh, Kafka or
operator is introduced without a concrete requirement.
