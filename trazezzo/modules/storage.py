"""Storage module — disk, fs, mount, LVM. Detects local vs network filesystems
and fstab entries that are configured but not currently mounted."""

from __future__ import annotations

import subprocess
import psutil

# Filesystem types that mean the mount is backed by the network / another host
NETWORK_FS_TYPES = {
    "nfs", "nfs4", "cifs", "smbfs", "smb2", "ncpfs",
    "afs", "afs3", "ceph", "cephfs", "glusterfs", "lustre",
    "fuse.rclone", "fuse.sshfs", "fuse.gocryptfs", "fuse.curlftpfs",
    "9p", "davfs", "davfs2", "ocfs2", "gfs", "gfs2", "autofs",
}


def classify_fs(fstype: str) -> str:
    """Classify a filesystem type as 'network' or 'local'."""
    fs = (fstype or "").lower()
    if fs in NETWORK_FS_TYPES or fs.startswith("nfs") or fs.startswith("fuse.") or fs.startswith("cifs"):
        return "network"
    return "local"


def get_unmounted_fstab_entries() -> list[dict]:
    """Parse /etc/fstab and report entries whose target is not currently mounted.

    Covers NAS/NFS/CIFS mounts configured on the host but currently down
    (server unreachable, mount failed at boot, etc.).
    """
    mounted_points = {p.mountpoint for p in psutil.disk_partitions()}
    mounted_devices = {p.device for p in psutil.disk_partitions()}
    entries = []

    try:
        with open("/etc/fstab") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split()
                if len(parts) < 4:
                    continue
                device, mountpoint, fstype = parts[0], parts[1], parts[2]
                if mountpoint in ("none", "/swapfile", "swap"):
                    continue
                if fstype in ("swap", "auto"):
                    if fstype == "swap":
                        continue
                # Normalize device: UUID=/LABEL= kept as-is
                is_mounted = mountpoint in mounted_points or device in mounted_devices
                if not is_mounted:
                    entries.append({
                        "device": device,
                        "mountpoint": mountpoint,
                        "fstype": fstype,
                        "fs_class": classify_fs(fstype),
                        "options": parts[3] if len(parts) > 3 else "",
                    })
    except FileNotFoundError:
        pass
    except Exception:
        pass

    return entries


def get_storage_overview() -> dict:
    """Disk usage + mounts + LVM info."""
    partitions = []
    for part in psutil.disk_partitions():
        # Skip pseudo filesystems that are not real storage
        if part.fstype in ("proc", "sysfs", "devpts", "tmpfs", "devtmpfs", "cgroup", "cgroup2", "efivarfs", "securityfs", "pstore", "bpf", "debugfs", "tracefs", "configfs", "fusectl", "hugetlbfs", "mqueue", "rpc_pipefs", "nsfs"):
            continue
        try:
            usage = psutil.disk_usage(part.mountpoint)
            partitions.append({
                "device": part.device,
                "mountpoint": part.mountpoint,
                "fstype": part.fstype,
                "fs_class": classify_fs(part.fstype),
                "opts": part.opts,
                "total_gb": round(usage.total / 1e9, 2),
                "used_gb": round(usage.used / 1e9, 2),
                "free_gb": round(usage.free / 1e9, 2),
                "percent": usage.percent,
            })
        except Exception:
            partitions.append({
                "device": part.device,
                "mountpoint": part.mountpoint,
                "fstype": part.fstype,
                "fs_class": classify_fs(part.fstype),
                "opts": part.opts,
                "error": "inaccessible",
            })

    # LVM
    lvm = {"pvs": [], "vgs": [], "lvs": []}
    try:
        result = subprocess.run(["pvs", "--noheadings"], capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            lvm["pvs"] = [line.strip() for line in result.stdout.strip().split("\n") if line.strip()]
        result = subprocess.run(["vgs", "--noheadings"], capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            lvm["vgs"] = [line.strip() for line in result.stdout.strip().split("\n") if line.strip()]
        result = subprocess.run(["lvs", "--noheadings"], capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            lvm["lvs"] = [line.strip() for line in result.stdout.strip().split("\n") if line.strip()]
    except Exception:
        pass

    # Block devices
    block_devices = []
    try:
        result = subprocess.run(["lsblk", "-b", "--json"], capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            import json
            block_devices = json.loads(result.stdout).get("blockdevices", [])
    except Exception:
        pass

    return {
        "partitions": partitions,
        "unmounted": get_unmounted_fstab_entries(),
        "lvm": lvm,
        "block_devices": block_devices,
    }
