"""Public import path for the shared learner context service."""

from deeptutor.learner_context.classifier import classify_interaction
from deeptutor.learner_context.client import LearnerContextClient
from deeptutor.learner_context.prompt import combine_mode_and_learner_context
from deeptutor.learner_context.models import (
    IntegrationCredential,
    InteractionStyle,
    LearnerStyleProfile,
    StyleLabel,
    StyleRecord,
)
from deeptutor.learner_context.service import (
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

__all__ = [
    "DEFAULT_STYLE_INSTRUCTIONS",
    "IntegrationCredential",
    "InteractionStyle",
    "LearnerStyleProfile",
    "StyleLabel",
    "StyleRecord",
    "LearnerContextClient",
    "classify_interaction",
    "combine_mode_and_learner_context",
    "clear_records",
    "clear_records_for_current_user",
    "delete_record",
    "delete_record_for_current_user",
    "export_profile",
    "get_profile",
    "get_prompt_context",
    "record_interaction_style",
    "set_enabled",
]
