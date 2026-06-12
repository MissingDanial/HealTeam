"""HealTeam agents package.

Public surface (import from `healteam_agents`, not from submodules):
- `BaseAgent`, `SessionContext`, `Hint`, `LLMClient` from `.base`
- 6 concrete agent classes from their respective modules
- `Orchestrator` from `.orchestrator`
- `build_case_context` from `.case_loader`
"""

from .base import (
    BaseAgent,
    DEFAULT_PROMPTS_DIR,
    Hint,
    LLMClient,
    SessionContext,
    load_system_prompt,
    make_placeholder_utterance,
    resolve_prompt_path,
)
from .assessor import assess_emotions, assess_relationships, parse_interventions
from .case_loader import build_case_context
from .defense_tracker import DefenseState, detect_defenses, detect_defenses_keyword
from .doctor import DoctorAgent
from .milestone_detector import detect_milestones, detect_milestones_keyword
from .report import generate_report
from .supervision import SupervisionOrchestrator
from .father import FatherAgent
from .mother import MotherAgent
from .observer import ObserverAgent
from .orchestrator import (
    DEFAULT_MAX_ROUNDS,
    FAMILY_ACTORS,
    MAX_SPEAKERS_PER_ROUND,
    Orchestrator,
    SPEAKING_ACTORS,
    TherapyState,
)
from .patient import PatientAgent
from .psychiatrist import PsychiatristAgent

__all__ = [
    # base
    "BaseAgent",
    "SessionContext",
    "Hint",
    "LLMClient",
    "DEFAULT_PROMPTS_DIR",
    "load_system_prompt",
    "make_placeholder_utterance",
    "resolve_prompt_path",
    # assessor
    "assess_emotions",
    "assess_relationships",
    "parse_interventions",
    # case loader
    "build_case_context",
    # defense tracker
    "DefenseState",
    "detect_defenses",
    "detect_defenses_keyword",
    # milestone detector
    "detect_milestones",
    "detect_milestones_keyword",
    # report
    "generate_report",
    # supervision
    "SupervisionOrchestrator",
    # concrete agents
    "DoctorAgent",
    "PsychiatristAgent",
    "ObserverAgent",
    "FatherAgent",
    "MotherAgent",
    "PatientAgent",
    # orchestrator
    "Orchestrator",
    "TherapyState",
    "DEFAULT_MAX_ROUNDS",
    "MAX_SPEAKERS_PER_ROUND",
    "FAMILY_ACTORS",
    "SPEAKING_ACTORS",
]
