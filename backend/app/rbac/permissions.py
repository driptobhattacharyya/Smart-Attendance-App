"""The permission table of docs/architecture-v5.md section 4.

Authorization always checks permissions, never role names. Built-in roles are copied into every
organization's ``roles`` table at signup so orgs can customise them later.
"""

from __future__ import annotations

from dataclasses import dataclass

ATTENDANCE_SELF = "attendance.self"
ATTENDANCE_VIEW_TEAM = "attendance.view.team"
ATTENDANCE_VIEW_ORG = "attendance.view.org"
REQUESTS_APPROVE_TEAM = "requests.approve.team"
REQUESTS_APPROVE_ORG = "requests.approve.org"
ATTENDANCE_CORRECT = "attendance.correct"
PEOPLE_VIEW_TEAM = "people.view.team"
PEOPLE_MANAGE = "people.manage"
PLACES_MANAGE = "places.manage"
POLICIES_MANAGE = "policies.manage"
REPORTS_EXPORT = "reports.export"
AUDIT_VIEW = "audit.view"
ORG_MANAGE = "org.manage"

ALL_PERMISSIONS: tuple[str, ...] = (
    ATTENDANCE_SELF,
    ATTENDANCE_VIEW_TEAM,
    ATTENDANCE_VIEW_ORG,
    REQUESTS_APPROVE_TEAM,
    REQUESTS_APPROVE_ORG,
    ATTENDANCE_CORRECT,
    PEOPLE_VIEW_TEAM,
    PEOPLE_MANAGE,
    PLACES_MANAGE,
    POLICIES_MANAGE,
    REPORTS_EXPORT,
    AUDIT_VIEW,
    ORG_MANAGE,
)

_ADMIN = tuple(p for p in ALL_PERMISSIONS if p != ORG_MANAGE)
_MANAGER = (
    ATTENDANCE_SELF,
    ATTENDANCE_VIEW_TEAM,
    REQUESTS_APPROVE_TEAM,
    PEOPLE_VIEW_TEAM,
    REPORTS_EXPORT,
)


@dataclass(frozen=True)
class BuiltinRole:
    key: str
    name: str
    permissions: tuple[str, ...]


BUILTIN_ROLES: tuple[BuiltinRole, ...] = (
    BuiltinRole("owner", "Owner", ALL_PERMISSIONS),
    BuiltinRole("admin", "Administrator", _ADMIN),
    BuiltinRole("manager", "Manager", _MANAGER),
    BuiltinRole("employee", "Employee", (ATTENDANCE_SELF,)),
)

BUILTIN_ROLE_KEYS = tuple(r.key for r in BUILTIN_ROLES)
