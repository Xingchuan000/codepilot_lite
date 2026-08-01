from typing import Literal

CompactionTrigger = Literal["soft_budget", "hard_budget", "provider_overflow", "manual", "recovery"]
