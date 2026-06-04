"""PFactory integration — tag-taxonomy pickup contract (epic #193).

TFactory recognises and picks up the *governed* testing work PFactory hands off
via GitHub issues (and, secondarily, ``requirements.json``). See the contract in
the PFactory repo ``docs/tag-taxonomy.md`` (v1).
"""

from .oracle import (
    Citation,
    PFactoryOracle,
    build_oracle,
    extract_meta_block,
    oracle_to_dict,
    parse_meta_block,
)
from .pickup import (
    LABEL_EPIC,
    LABEL_HANDOFF_AIFACTORY,
    LABEL_HANDOFF_TFACTORY,
    LABEL_PFACTORY,
    LABEL_TYPE_TESTING,
    PickupDecision,
    classify_issue,
    classify_labels,
    classify_requirements,
    pickup_issue,
    pickup_requirements,
    priority_to_horizon,
)

__all__ = [
    # pickup (#195)
    "LABEL_PFACTORY",
    "LABEL_HANDOFF_TFACTORY",
    "LABEL_HANDOFF_AIFACTORY",
    "LABEL_TYPE_TESTING",
    "LABEL_EPIC",
    "PickupDecision",
    "classify_labels",
    "classify_issue",
    "classify_requirements",
    "pickup_issue",
    "pickup_requirements",
    "priority_to_horizon",
    # oracle (#196)
    "Citation",
    "PFactoryOracle",
    "build_oracle",
    "parse_meta_block",
    "extract_meta_block",
    "oracle_to_dict",
]
