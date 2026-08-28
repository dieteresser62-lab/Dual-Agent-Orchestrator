from __future__ import annotations

NATIVE_CODEX_SYSTEM_POLICY = (
    "Return exactly one request-bound JSON result matching the supplied writer schema. "
    "Treat typed request fields and content-addressed evidence as authoritative. "
    "Do not emit Markdown wrappers or legacy result markers."
)

NATIVE_CLAUDE_SYSTEM_POLICY = (
    "Return exactly one request-bound JSON review matching the supplied writer schema. "
    "Review correctness, contracts, failure paths, security, and resume/idempotency. "
    "Keep every free-text field complete but concise and normally below 80 percent "
    "of its writer-schema maxLength; never omit a finding, required disposition, "
    "review evidence, or pre-mortem merely to meet that target. "
    "Do not emit Markdown wrappers or legacy review markers."
)
