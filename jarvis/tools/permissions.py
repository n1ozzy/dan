"""Tool permission classes and policy placeholder."""

from __future__ import annotations

from enum import StrEnum


class PermissionClass(StrEnum):
    SAFE_READ = "safe_read"
    SAFE_STATUS = "safe_status"
    FILE_READ = "file_read"
    FILE_WRITE = "file_write"
    SHELL_READ = "shell_read"
    SHELL_WRITE = "shell_write"
    NETWORK = "network"
    DESTRUCTIVE = "destructive"


class PermissionPolicy:
    def requires_approval(self, permission: PermissionClass) -> bool:
        return permission not in {
            PermissionClass.SAFE_READ,
            PermissionClass.SAFE_STATUS,
            PermissionClass.FILE_READ,
        }
