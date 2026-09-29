# Trazezzo Roadmap

## [Planned] Deployment Mode Support: VM / Kubernetes / Hybrid

### Goal
Add deployment mode support so Trazezzo can run in different environments: VM/Bare Metal (current), Kubernetes/Container, or Hybrid (VM + K8s API).

### Motivation
Trazezzo currently assumes bare-metal/VM access (psutil, journald, auditd, eBPF). Deploying in K8s/container loses ~80% of capture sources. A mode-based adaptive UI lets users choose the deployment context, showing only relevant features.

### Design: Mode-based adaptive UI (single codebase)

**3 modes:**
1. **VM/Bare Metal** (default, current behavior)
2. **Kubernetes/Container** — K8s API as primary data source
3. **Hybrid** — VM capture + K8s API awareness

**Implementation approach:**
- `config.json` → `deployment_mode: "vm" | "k8s" | "hybrid"`
- `base.html` → conditional nav items `{% if mode == 'k8s' %}`
- Module adapter pattern: each module has VM + K8s backend
  - `modules/services.py` → `list_services_vm()` / `list_services_k8s()`
  - `modules/events.py` → `capture_vm()` / `capture_k8s()`
- New `modules/k8s_client.py` — kubeconfig loader, kubectl wrapper
- Settings page — mode selector with auto-detect

### Feature matrix per mode

| Feature | VM Mode | K8s Mode | Hybrid |
|---|---|---|---|
| Dashboard (CPU/RAM/disk) | psutil (host) | K8s metrics API | Both |
| SSH Terminal | ✅ | ✅ (SSH to nodes) | ✅ |
| File Explorer | ✅ | ✅ (container FS) | ✅ |
| Services | systemctl | kubectl get pods | Both |
| Events | journald + auditd + eBPF | K8s event stream | Both |
| Cron Jobs | crontab | K8s CronJob | Both |
| Networking | ip/iptables | kubectl svc/ingress | Both |
| Storage | df/LVM | PVC/storage class | Both |
| Processes | ps/pstree | kubectl top pods | Both |
| SSL Certs | certbot | cert-manager | Both |
| Accounts | /etc/passwd | K8s RBAC | Both |
| AI Agent | ✅ | ✅ + kubectl context | ✅ |

### Why adaptive > separate UIs

| | Separate UI | Adaptive |
|---|---|---|
| Codebase | 2x | 1x |
| Shared features (SSH, Explorer, Agent) | Duplicated | Reused |
| Migration VM→K8s | Relearn UI | Toggle mode |
| Maintenance | 2x | 1x + conditional logic |

### Status
Roadmap item. Should only implement when a real K8s cluster is available for testing. Recommended approach: Option C (Hybrid) — VM with K8s API as additional data source.
