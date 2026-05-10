"""Переиспользуемые тестовые хелперы для сценариев handlers."""

import asyncio
from types import SimpleNamespace


def run_async(coro):
    """Запускает async-корутины в синхронных тестах."""
    return asyncio.run(coro)


class FakeSentMessage:
    async def delete(self):
        return None


class FakeMessage:
    def __init__(self, text=None):
        self.text = text
        self.calls = []
        self.deleted = False

    async def reply_text(self, text, **kwargs):
        self.calls.append({"text": text, **kwargs})
        return FakeSentMessage()

    async def delete(self):
        self.deleted = True


class FakeCallbackQuery:
    def __init__(self, data=""):
        self.data = data
        self.message = FakeMessage()
        self.answers = 0
        self.edits = []
        self.edit_reply_markup_calls = []

    async def answer(self, *args, **kwargs):
        self.answers += 1

    async def edit_message_text(self, text, **kwargs):
        self.edits.append({"text": text, **kwargs})

    async def edit_message_reply_markup(self, **kwargs):
        self.edit_reply_markup_calls.append(kwargs)


def ctx(user_data=None):
    return SimpleNamespace(user_data=user_data or {})


def update_with_message(text, user_id=777):
    msg = FakeMessage(text=text)
    return SimpleNamespace(message=msg, effective_user=SimpleNamespace(id=user_id))


def update_with_query(data, user_id=777):
    query = FakeCallbackQuery(data=data)
    return SimpleNamespace(callback_query=query, effective_user=SimpleNamespace(id=user_id))
