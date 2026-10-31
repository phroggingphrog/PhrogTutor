from __future__ import annotations

from deeptutor.multi_user.context import reset_current_user, set_current_user
from deeptutor.multi_user.models import CurrentUser, UserScope
from deeptutor.services import chat_mastery


def _install_user(user_id: str, root):
    scope = UserScope(kind="user", user_id=user_id, root=root)
    return set_current_user(CurrentUser(id=user_id, username=user_id, role="user", scope=scope))


def test_chat_turn_does_not_schedule_classifier_before_explicit_opt_in(tmp_path, monkeypatch):
    _user_token = _install_user("u_no_consent", tmp_path)
    try:
        scheduled = []

        class _Task:
            def add_done_callback(self, _callback):
                pass

        monkeypatch.setattr(chat_mastery.asyncio, "create_task", lambda coroutine: (coroutine.close(), scheduled.append(coroutine), _Task())[2])
        chat_mastery.record_turn("s", "t", "synthetic learner text", "synthetic assistant text")
        assert scheduled == []
        assert not chat_mastery._style_collection_enabled()
    finally:
        reset_current_user(_user_token)


def test_chat_turn_schedules_classifier_only_after_explicit_opt_in(tmp_path, monkeypatch):
    _user_token = _install_user("u_consent", tmp_path)
    try:
        assert chat_mastery.set_style_collection_enabled(True)
        scheduled = []

        class _Task:
            def add_done_callback(self, callback):
                self.callback = callback

        task = _Task()

        def record_create(coroutine):
            coroutine.close()
            scheduled.append(coroutine)
            return task

        monkeypatch.setattr(chat_mastery.asyncio, "create_task", record_create)
        chat_mastery.record_turn(
            "s", "t", "synthetic learner text", "synthetic assistant text", style_collection_opt_in=True
        )
        assert len(scheduled) == 1
        assert task in chat_mastery._TASKS
        chat_mastery._TASKS.discard(task)
    finally:
        reset_current_user(_user_token)


def test_passed_opt_in_flag_cannot_bypass_disabled_database_consent(tmp_path, monkeypatch):
    _user_token = _install_user("u_consent_off", tmp_path)
    try:
        scheduled = []
        monkeypatch.setattr(chat_mastery.asyncio, "create_task", lambda coroutine: (coroutine.close(), scheduled.append(coroutine))[1])
        chat_mastery.record_turn(
            "s", "t", "synthetic learner text", "synthetic assistant text", style_collection_opt_in=True
        )
        assert scheduled == []
    finally:
        reset_current_user(_user_token)
