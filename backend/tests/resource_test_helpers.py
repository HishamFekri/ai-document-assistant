"""Admission doubles for earlier batches; no Redis or database access."""

from unittest.mock import MagicMock, patch


def install_resource_mocks(stack):
    from app.services import resource_admission as admission
    def permit(user_id, category, connection=None):
        result = MagicMock()
        result.user_id = user_id
        result.category = category
        return result
    stack.enter_context(patch.object(admission, "consume_user_rate"))
    stack.enter_context(patch.object(admission, "acquire_permit", side_effect=permit))
