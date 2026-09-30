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
    ("codex", AgentRoleName.REVIEWER): RoleBinding(  # allowlist:provider -- certification data: isolated reviewer binding
        AgentRoleName.REVIEWER,
        _ROLE_BINDINGS[AgentRoleName.REVIEWER].contract,
        _ROLE_BINDINGS[AgentRoleName.REVIEWER].policy,
        MappingProxyType({
            "profile": "dao-reviewer",
            "read_roots": ":minimal,<codex-package-root>,:workspace_roots/.",  # allowlist:provider -- certification data: symbolic read roots
            "sandbox_flag": "absent",
            "network": "disabled",
            "user_config": "ignored",
            "exec_rules": "ignored",
            "project_docs": "disabled",
            "shell_environment": "core",
            "disabled_features": "apps,plugins,multi_agent,goals,browser_use,computer_use,image_generation,hooks,skill_search,tool_suggest,remote_plugin",
        }),
    ),
    ("claude", AgentRoleName.IMPLEMENTER): RoleBinding(  # allowlist:provider -- certification data: sandboxed implementer binding
        AgentRoleName.IMPLEMENTER,
        _ROLE_BINDINGS[AgentRoleName.IMPLEMENTER].contract,
        _ROLE_BINDINGS[AgentRoleName.IMPLEMENTER].policy,
        MappingProxyType({
            "tools": "Read,Edit,Write,Glob,Grep,Bash",
            "permission_mode": "acceptEdits",
            "permission_prompts": "none",
            "file_tool_deny": "protected-paths-exact-and-recursive",
            "cli_file_tool_deny": "same-rules-as-settings",
            "block_reads_outside_working_directories": "true",
            "restricted": "true",
            "safe_mode": "true",
            "sandbox": "enabled,failIfUnavailable,autoAllowBashIfSandboxed",
            "hooks": "disabled",
            "unsandboxed_commands": "false",
            "excluded_commands": "empty",
            "network": "empty-strict-allowlist",
            "protected_paths": "git,gitdir,common-dir,orchestrator,records,checkpoints,queue,inbox,outbox,evidence",
            "filesystem_deny_write": "protected-paths",
            "environment": "HOME,USER,LOGNAME,PATH,LANG,TERM",
            "credentials": "ANTHROPIC_API_KEY,ANTHROPIC_AUTH_TOKEN,CLAUDE_CODE_OAUTH_TOKEN",  # allowlist:provider -- certification data: implementer credentials
        }),
    ),
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
