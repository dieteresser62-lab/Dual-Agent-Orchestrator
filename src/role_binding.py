"""Role contract, permissions and policy, separate from provider transport."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from agent_roles import AgentRoleName
from prompts import NATIVE_REVIEWER_SYSTEM_POLICY, NATIVE_IMPLEMENTER_SYSTEM_POLICY


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
        "native-agent-implementer-result-v3",
        NATIVE_IMPLEMENTER_SYSTEM_POLICY,
        MappingProxyType({"sandbox": "workspace-write"}),
    ),
    AgentRoleName.REVIEWER: RoleBinding(
        AgentRoleName.REVIEWER,
        "native-agent-review-result-v3",
        NATIVE_REVIEWER_SYSTEM_POLICY,
        MappingProxyType({
            "tools": "Read",
            "allowed_tools": "Read",
            "disallowed_tools": "Bash,Edit,Write,NotebookEdit,Grep,Glob",
            "permission_mode": "dontAsk",
            "restricted_flag": "--restricted",
        }),
    ),
}

# This registry names only the measured legacy pairs. New pairs require their
# own explicit rights and policy binding before they can be admitted.
_PAIR_BINDINGS: dict[tuple[str, AgentRoleName], RoleBinding] = {
    ("codex", AgentRoleName.IMPLEMENTER): _ROLE_BINDINGS[AgentRoleName.IMPLEMENTER],  # allowlist:provider -- certification data: measured implementer binding
    ("claude", AgentRoleName.REVIEWER): _ROLE_BINDINGS[AgentRoleName.REVIEWER],  # allowlist:provider -- certification data: measured reviewer binding
    ("antigravity", AgentRoleName.REVIEWER): _ROLE_BINDINGS[AgentRoleName.REVIEWER],
}


def binding_for(provider: str, role: AgentRoleName) -> RoleBinding:
    if not isinstance(provider, str) or not provider:
        raise TypeError("provider must be a nonempty string")
    return _PAIR_BINDINGS[(provider, role)]


def binding_for_role(role: AgentRoleName) -> RoleBinding:
    if not isinstance(role, AgentRoleName):
        raise TypeError("agent role must be an AgentRoleName")
    return _ROLE_BINDINGS[role]
