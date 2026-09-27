"""Role contract, permissions and policy, separate from provider transport."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from agent_roles import AgentRoleName
from prompts import NATIVE_CLAUDE_SYSTEM_POLICY, NATIVE_CODEX_SYSTEM_POLICY


@dataclass(frozen=True, slots=True)
class RoleBinding:
    role: AgentRoleName
    contract: str
    policy: str
    permissions: Mapping[str, str]

    @property
    def policy_sha256(self) -> str:
        return hashlib.sha256(self.policy.encode("utf-8")).hexdigest()

    @property
    def rights_sha256(self) -> str:
        encoded = json.dumps(
            dict(self.permissions), sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


_ROLE_BINDINGS = {
    AgentRoleName.IMPLEMENTER: RoleBinding(
        AgentRoleName.IMPLEMENTER,
        "native-agent-codex-result-v2",
        NATIVE_CODEX_SYSTEM_POLICY,
        MappingProxyType({"sandbox": "workspace-write"}),
    ),
    AgentRoleName.REVIEWER: RoleBinding(
        AgentRoleName.REVIEWER,
        "native-agent-review-result-v2",
        NATIVE_CLAUDE_SYSTEM_POLICY,
        MappingProxyType({
            "tools": "Read",
            "allowed_tools": "Read",
            "disallowed_tools": "Bash,Edit,Write,NotebookEdit,Grep,Glob",
            "permission_mode": "dontAsk",
        }),
    ),
}


def binding_for_role(role: AgentRoleName) -> RoleBinding:
    if not isinstance(role, AgentRoleName):
        raise TypeError("agent role must be an AgentRoleName")
    return _ROLE_BINDINGS[role]
