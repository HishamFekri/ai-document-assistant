from fastapi import Depends

from app.routes.auth import get_current_user
from app.services.resource_admission import user_operation


def admit_chat(current_user=Depends(get_current_user)):
    with user_operation(current_user.id, "chat", rate=False) as permit:
        yield permit


def admit_search(current_user=Depends(get_current_user)):
    with user_operation(current_user.id, "search", rate=False) as permit:
        yield permit


def admit_summary(current_user=Depends(get_current_user)):
    with user_operation(current_user.id, "summary", rate=False) as permit:
        yield permit
