# Market Comparison

**Document status:** Point-in-time product comparison

**Research date:** 2026-08-13

**Evidence policy:** official product pages and documentation only

## 1. Executive Summary

The Dual-Agent Task Orchestrator occupies a narrower category than most products in this comparison. Codex, Claude Code, Google Antigravity, GitHub Copilot, Cursor, OpenHands, and aider primarily provide an agent, agent workspace, development interface, or agent platform. This project is a local workflow control plane that invokes three of those agent surfaces in fixed roles and adds deterministic evidence, asymmetric independent reviews, resumable gates, and exact local commit authorization.

Its strongest differentiator is the combination of:

- named cross-provider separation between implementer and two reviewers;
- canonical Git evidence and review decisions bound to exact SHA-256 diff fingerprints;
- orchestrator-owned validation reused across reviewers;
- read-only reviewer workspaces;
- persisted finding ownership and exact-step resume;
- path-bounded, locally verified slice commits with no push or merge authority.

That control comes with deliberate trade-offs. The orchestrator has no IDE, hosted execution fleet, browser agent, PR interface, model marketplace, distributed queue, or parallel slice execution. Several market products are substantially stronger in those areas.

## 2. How to Read This Comparison

This is not a benchmark of model intelligence, generated-code quality, price, or speed. Those properties change quickly and require controlled workloads. The comparison instead asks which workflow capabilities are explicitly described by current official documentation.

Terms used in the matrices:

- **Built in:** the cited documentation describes the capability as a product feature.
- **Configurable:** the capability can be assembled with product configuration, hooks, SDKs, or separate product features, but is not the default topology being compared.
- **Not evidenced:** the reviewed official sources do not establish the specific capability. This does not prove that it is impossible.
- **Different scope:** the product solves a related problem at another layer.

Product names refer to the surfaces described in the linked sources, not every capability sold under the same brand.

## 3. Comparison Set

| Product or surface | Primary category | Why it is comparable |
|---|---|---|
| Dual-Agent Task Orchestrator | Local multi-agent workflow control plane | Baseline: bounded implementation, review, validation, resume, and Git transaction. |
| OpenAI Codex | Local/cloud coding agent and multi-agent command center | Supplies the implementer used here and supports parallel agent work. |
| Claude Code agent teams | Terminal-centered multi-agent coding environment | Supplies the primary reviewer used here and supports explicit agent coordination. |
| Google Antigravity 2.0 | Standalone agent command center and CLI/IDE ecosystem | Supplies the independent reviewer used here and supports projects, worktrees, and subagents. |
| GitHub Copilot cloud agent and code review | GitHub-native coding and review services | Automates issue-to-PR work and review inside the hosting platform. |
| Cursor Cloud Agents | Hosted coding-agent execution and PR workflow | Provides parallel cloud VMs, rich artifacts, integrations, and approval tooling. |
| OpenHands | Open-source agent SDK, runtime, CLI, and cloud platform | Provides model-agnostic local, self-hosted, and cloud agent infrastructure. |
| aider | Local terminal pair programmer | Provides a lightweight multi-model editing workflow with tight Git integration. |

## 4. Operating-Model Matrix

| Product | Execution surface | Runtime locality | Agent/model topology | Parallel work | Primary Git handoff |
|---|---|---|---|---|---|
| Dual-Agent Orchestrator | Python CLI and FIFO watcher | Target worktree plus disposable local reviewer copies | Fixed Codex → Claude → Antigravity roles; role CLIs independently configured | Slices sequential; provider-internal concurrency outside its control | Verified local slice commits only |
| OpenAI Codex | CLI, IDE, desktop app, and cloud tasks | Local sandbox or isolated cloud environments | OpenAI coding agents; multiple isolated threads/worktrees | Built in across tasks | Cloud commit, local checkout, or pull request |
| Claude Code agent teams | Terminal, desktop, IDE, web, and automation surfaces | Primarily local sessions; web/cloud surface also available | Claude team lead, teammates, and subagents with separate contexts | Built in; shared task list and direct teammate messaging | Repository edits under the user's Git workflow |
| Google Antigravity 2.0 | Standalone app, CLI, and IDE ecosystem | Project-scoped local/worktree execution plus managed-agent options | Multiple conversations, dynamic subagents, custom agents, skills, and MCP | Built in across projects and subagents | Project/worktree changes; external SCM actions depend on surface |
| GitHub Copilot cloud agent | GitHub issue/PR and connected development surfaces | Ephemeral GitHub Actions environment | Copilot cloud agent; separate Copilot code-review service and third-party agents | Multiple assignments can run independently | Branch and pull request on GitHub |
| Cursor Cloud Agents | Web, desktop, mobile, chat integrations, SCM, and API | Cursor-managed isolated VMs | Curated selectable models and subagents | Built in; many cloud agents may run in parallel | Separate branch pushed for merge-ready PR handoff |
| OpenHands | Python/REST SDK, CLI, local GUI, cloud, and enterprise | Local, managed cloud, Docker, or Kubernetes | Model-agnostic agents and custom multi-agent applications | Supported by SDK/platform design | Configurable by the application or GitHub workflow |
| aider | Terminal chat | Local worktree | Flexible model selection; architect/editor two-model mode | Not the primary documented workflow | Automatic local commits by default |

## 5. Governance and Evidence Matrix

| Capability | Dual-Agent Orchestrator | Codex | Claude teams | Antigravity | GitHub Copilot | Cursor Cloud | OpenHands | aider |
|---|---|---|---|---|---|---|---|---|
| Fixed implementer/reviewer separation | Built in across three named providers | Not evidenced in the reviewed product flow | Configurable with teammates/subagents | Configurable with custom agents/subagents | Coding and review are separate services | Configurable with review and approval agents | Configurable in an application | Architect/editor separation is available, but not an independent approval chain |
| Review bound to an exact canonical diff fingerprint | Built in | Not evidenced | Not evidenced | Not evidenced | PR/commit context, but this exact contract is not evidenced | PR/run context, but this exact contract is not evidenced | Configurable | Not evidenced |
| One deterministic validation attestation reused by all reviewers | Built in | Terminal/test evidence is available, but this exact ownership model is not evidenced | Hooks and tools are available, but this exact ownership model is not evidenced | Agent verification artifacts are built in | Agent tests and code review exist, but are separate flows | Build/test artifacts and review systems exist | Tools, events, security, and tracing are configurable | The editing agent can run tests; no separate shared attestation is evidenced |
| Reviewer source workspace is read-only by construction | Built in | Not evidenced as a reviewer role invariant | Plan mode can be read-only; per-team permission behavior differs | Scoped permissions are built in; fixed reviewer immutability is not evidenced | Code review comments rather than a writable implementation session | Read-only exploratory turns exist; fixed cross-reviewer isolation is not evidenced | Action confirmation and sandboxing are configurable | Ask mode is read-only, but not a mandatory reviewer stage |
| Persisted owner-specific finding lifecycle | Built in | Different scope | Different scope | Artifact feedback is built in | PR review threads provide platform lifecycle | PR review and approval systems provide platform lifecycle | Configurable | Chat/Git history rather than a structured reviewer-owned lifecycle |
| Fingerprint-bound human policy gates | Built in | Approval modes exist; this exact binding is not evidenced | Permission prompts and plan approval exist; this exact binding is not evidenced | Scoped approvals exist; this exact binding is not evidenced | Repository and organization policies apply in GitHub | Approval policies and risk thresholds are built in | Configurable | Interactive confirmation rather than persisted policy state |
| Exact-step resume after quota/process/user halt | Built in | Session continuity is available; this exact state machine is not evidenced | Agent-team resume has documented limitations | Persistent projects/conversations are available | Cloud task/PR continuation is platform-managed | Cloud run and follow-up history are available | Conversation persistence is configurable | Chat history can be restored; this exact state machine is not evidenced |
| Commit restricted to reviewed path allowlist | Built in | Not evidenced | Not evidenced | Project/worktree scope, but exact commit allowlists are not evidenced | PR diff is the review boundary | PR diff is the review boundary | Configurable | Automatic commits cover the editing session, not a separately approved allowlist |

## 6. Product Profiles

### 6.1 OpenAI Codex

OpenAI describes Codex as a coding agent available across CLI, IDE, desktop, and cloud surfaces. Cloud tasks run in isolated environments, and the Codex app supports multiple agents in parallel with built-in worktrees. Completed work can be reviewed, revised, checked out locally, or turned into a pull request. This makes Codex broader and more polished as an implementation workspace than this orchestrator.

The orchestrator uses Codex for a narrower responsibility: planning, implementation, correction, and final reporting inside a state machine owned elsewhere. Its added value is not another Codex execution surface, but independent Claude and Antigravity verdicts plus deterministic commit authorization.

Official sources: [Introducing the Codex app](https://openai.com/index/introducing-the-codex-app/), [Introducing Codex](https://openai.com/index/introducing-codex/), [Codex CLI overview](https://help.openai.com/en/articles/11096431-openai-codex-cli-getting-started).

### 6.2 Claude Code agent teams

Claude Code agent teams coordinate a lead and multiple independent Claude sessions through a shared task list and direct messaging. They are strong for parallel research, review, competing hypotheses, and file-separated implementation. Teams can require plan approval and enforce lifecycle rules with hooks. Anthropic currently labels the feature experimental and documents limitations in resume, task-state synchronization, shutdown, nesting, and fixed leadership.

That topology favors collaborative parallel execution. The Dual-Agent Orchestrator instead favors a sequential chain with provider diversity and immutable role boundaries. Claude cannot promote itself from reviewer to implementer, and its approval is insufficient without Antigravity's later verdict for the same fingerprint.

Official sources: [Claude Code agent teams](https://code.claude.com/docs/en/agent-teams), [Claude Code subagents](https://code.claude.com/docs/en/sub-agents), [How Claude Code works](https://code.claude.com/docs/en/how-claude-code-works).

### 6.3 Google Antigravity 2.0

Google positions Antigravity 2.0 as a standalone command center for synchronous and asynchronous agents. Projects can span folders, use Git worktrees, apply scoped settings and permissions, and run dynamic subagents. The broader ecosystem includes CLI and IDE surfaces, browser interaction, artifacts, scheduled tasks, skills, hooks, and MCP integration.

Antigravity therefore offers a richer operator interface, parallelism, and interactive artifact experience. In this project it is deliberately reduced to one independent, read-only closing reviewer after Claude. The fixed ordering and evidence contract come from the orchestrator, not from Antigravity's general agent manager.

Official sources: [Antigravity 2.0 overview](https://antigravity.google/docs/overview), [Antigravity 2.0 features](https://antigravity.google/docs/features?app=antigravity), [Antigravity CLI agents](https://antigravity.google/docs/cli/commands/agents?hl=en), [Google developer announcement](https://developers.googleblog.com/build-with-google-antigravity-our-new-agentic-development-platform/).

### 6.4 GitHub Copilot cloud agent and code review

GitHub's cloud agent can take an issue, explore the repository, implement changes in an ephemeral GitHub Actions environment, run tests and linters, and open a pull request. Copilot code review is a distinct review surface with repository context, configurable instructions, skills, and MCP access. This is compelling where GitHub issues, PRs, permissions, and organization policy are the natural control plane.

The Dual-Agent Orchestrator is local-first and hosting-provider-independent. It stops at verified local commits and supplies stricter slice-path and fingerprint invariants, but lacks GitHub's collaboration, PR review, organization administration, and hosted execution.

Official sources: [Choosing the right GitHub AI tool](https://docs.github.com/en/copilot/concepts/tools/ai-tools), [About Copilot code review](https://docs.github.com/en/copilot/concepts/agents/code-review), [Copilot cloud-agent troubleshooting and environment](https://docs.github.com/en/copilot/how-tos/use-copilot-agents/cloud-agent/troubleshoot-cloud-agent), [Cloud-agent firewall](https://docs.github.com/en/copilot/how-tos/copilot-on-github/customize-copilot/customize-cloud-agent/customize-the-agent-firewall).

### 6.5 Cursor Cloud Agents

Cursor Cloud Agents run in isolated managed VMs with repositories, dependencies, secrets, network policy, MCP servers, hooks, browser/desktop control, and rich artifacts. Many agents can run in parallel, including multi-repository tasks. They work on separate branches, push for handoff, and produce merge-ready pull requests. Cursor also offers Bugbot, Security Agents, and risk-based PR Routing & Approval.

Cursor consequently covers more of the hosted engineering lifecycle. Its official documentation does not establish the same fixed cross-provider sequential review, one-attestation reuse, or local exact-path commit transaction. Teams that need managed capacity, rich artifacts, integrations, and PR automation may prefer Cursor; teams prioritizing a small auditable local control plane may prefer this orchestrator.

Official sources: [Cursor Cloud Agents](https://cursor.com/docs/cloud-agent), [Cursor PR Routing & Approval](https://cursor.com/docs/approval-agents).

### 6.6 OpenHands

OpenHands is the most directly extensible platform in the set. Its MIT-licensed Software Agent SDK exposes Python and REST APIs, predefined coding tools, local or cloud execution, and an agent server deployable with Docker or Kubernetes. It is model-agnostic and explicitly supports custom behaviors and major multi-agent tasks.

OpenHands is therefore a strong foundation for building a generalized or self-hosted agent platform. Reproducing this project's exact workflow on OpenHands would be an application design: role separation, fingerprint rules, reviewer isolation, finding ownership, gates, and commit authorization would need to be configured or implemented. The Dual-Agent Orchestrator provides those opinions out of the box but is far less general.

Official sources: [OpenHands Software Agent SDK](https://docs.openhands.dev/sdk/index), [OpenHands quick start](https://docs.openhands.dev/overview/quickstart), [OpenHands runtime architecture](https://docs.openhands.dev/openhands/usage/architecture/runtime).

### 6.7 aider

aider is a local terminal pair programmer with broad model support and tight Git integration. Its architect mode separates a planning model from an editor model, while ask mode is non-writing. By default, aider creates descriptive commits for edits and provides direct undo and diff commands.

That makes aider much lighter to install, understand, and use for interactive development. It does not document a mandatory independent two-reviewer chain, fingerprint-bound attestations, structured finding ownership, or exact-step gate state. It is best viewed as an efficient editing agent rather than a workflow governance layer.

Official sources: [aider chat modes](https://aider.chat/docs/usage/modes.html), [aider Git integration](https://aider.chat/docs/git.html).

## 7. Where the Dual-Agent Orchestrator Is Strongest

The project is particularly well suited when all of the following matter:

- work must remain in a local repository until a human chooses an external Git action;
- the implementer must not be the sole reviewer;
- reviewer diversity across providers is desired rather than multiple instances of one platform;
- tests must be run by a deterministic controller and tied to the exact reviewed diff;
- small, path-bounded commits are preferable to one large autonomous change;
- quota interruptions, restarts, and user gates must resume without replaying completed side effects;
- audit evidence should be both machine-readable and reviewable in committed Markdown.

## 8. Where Other Products Are Stronger

Choose or combine another product when the dominant requirement is:

| Requirement | Stronger fit from this comparison |
|---|---|
| Polished multi-agent desktop command center and parallel worktrees | OpenAI Codex app or Google Antigravity 2.0 |
| Claude-native collaborative teams with direct inter-agent messaging | Claude Code agent teams |
| GitHub issue-to-PR automation and organization-native review | GitHub Copilot cloud agent and code review |
| Managed parallel VMs, rich UI/browser artifacts, integrations, and PR automation | Cursor Cloud Agents |
| Model-agnostic SDK, self-hosting, Docker/Kubernetes, or custom agent products | OpenHands |
| Minimal local terminal pairing with convenient automatic Git history | aider |
| Parallel implementation throughput | Codex, Claude teams, Antigravity, Cursor, or an OpenHands-based design |

## 9. Positioning and Complementarity

The orchestrator is best described as a **policy-enforcing, evidence-bound local delivery pipeline for coding agents**. It is not a general-purpose multi-agent framework and should not market itself as a superior replacement for the products it invokes.

The relationship is often complementary:

- Codex, Claude, and Antigravity remain the reasoning and coding engines.
- A hosting product can still receive the verified local commits in a later human-controlled PR step.
- OpenHands could provide a future alternative execution substrate if the fixed adapter boundary were deliberately generalized.
- Repository hooks and CI remain useful as defense in depth after the orchestrator's local validation.

The defensible product claim is process integrity: the system can show which bounded changes were validated, which evidence each reviewer saw, who owned every finding, why a gate stopped progress, and which exact paths entered the resulting local commit.

## 10. Decision Guide

Use the Dual-Agent Task Orchestrator when you answer “yes” to most of these questions:

1. Must the work remain local and end in local commits rather than automatic PRs?
2. Do you require two ordered reviewer roles independent of the implementer?
3. Must validation be generated once per exact diff and reused as immutable evidence?
4. Do interruptions need an explicit persisted state machine and resumable gates?
5. Is a strict per-slice path allowlist more important than broad agent freedom?
6. Are you willing to trade parallel execution and a rich UI for traceability and control?

If the answer is mostly “no,” a single coding agent, hosted cloud agent, IDE-centered platform, or general agent SDK will usually be simpler.

## 11. Maintenance Policy

This comparison is time-sensitive. Reverify it before using it for procurement, pricing, security certification, or a public competitive claim. At minimum, review each official source and update the research date whenever a compared product changes its execution model, review features, persistence, Git behavior, licensing, or deployment options.

Absence claims must remain qualified as “not evidenced in the reviewed official sources.” Marketing language and vendor benchmark claims should not be converted into comparative quality claims without an independent evaluation.
