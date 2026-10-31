"""Harness-neutral access to learner interaction context."""

from .prompt import combine_mode_and_learner_context
from .client import LearnerContextClient
from .service import (
    DEFAULT_STYLE_INSTRUCTIONS,
    clear_records,
    clear_records_for_current_user,
    delete_record,
    delete_record_for_current_user,
    export_profile,
    get_profile,
    get_prompt_context,
    record_interaction_style,
    set_enabled,
)

record_interaction = record_interaction_style
profile = get_profile
prompt_context = get_prompt_context

__all__ = [
    "DEFAULT_STYLE_INSTRUCTIONS",
    "LearnerContextClient",
    "clear_records",
    "clear_records_for_current_user",
    "combine_mode_and_learner_context",
    "delete_record",
    "delete_record_for_current_user",
    "export_profile",
    "get_profile",
    "get_prompt_context",
    "profile",
    "prompt_context",
    "record_interaction",
    "record_interaction_style",
    "set_enabled",
]
