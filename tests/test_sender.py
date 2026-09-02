"""Odosielanie: po TimedOut/RetryAfter žiadny druhý pokus (duplicitné výzvy)."""
import asyncio

from telegram.error import NetworkError, RetryAfter, TimedOut

from trener.app import make_sender


class FakeBot:
    def __init__(self, fails):
        self.fails = list(fails)
        self.sent = []

    async def send_message(self, chat_id, text):
        if self.fails:
            raise self.fails.pop(0)
        self.sent.append(text)


def test_no_retry_after_timeout():
    bot = FakeBot([TimedOut()])
    asyncio.run(make_sender(bot, 1)("x"))
    assert bot.sent == []
    bot = FakeBot([RetryAfter(3)])
    asyncio.run(make_sender(bot, 1)("x"))
    assert bot.sent == []


def test_retry_after_connection_error():
    bot = FakeBot([NetworkError("conn refused")])
    asyncio.run(make_sender(bot, 1)("x"))
    assert bot.sent == ["x"]
