"""Planning policy values not yet migrated to the target defaults."""

# This remaining deviation is intentionally outside U4.  The cross-run
# finding machinery is gone; a later work unit will lower this independent
# outer acceptance-review policy from 64 to the target default of six.
MAX_ACCEPTANCE_REVIEWS = 64


__all__ = ["MAX_ACCEPTANCE_REVIEWS"]
