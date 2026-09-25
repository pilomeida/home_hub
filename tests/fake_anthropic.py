"""Scripted stand-in for AsyncAnthropic: returns queued responses in order
and records every request's kwargs."""

import itertools

_ids = itertools.count(1)


class Block:
    def __init__(self, type, **kw):
        self.type = type
        self.__dict__.update(kw)


class Usage:
    input_tokens = 100
    output_tokens = 20


class Response:
    def __init__(self, stop_reason, content):
        self.stop_reason = stop_reason
        self.content = content
        self.usage = Usage()


def text_response(text):
    return Response("end_turn", [Block("thinking", thinking=""), Block("text", text=text)])


def tool_response(*calls):
    """calls: (name, input_dict) pairs -> one assistant turn with parallel tool_use blocks."""
    return Response("tool_use", [Block("tool_use", id=f"tu_{next(_ids)}", name=n, input=i) for n, i in calls])


def refusal_response():
    return Response("refusal", [])


class _Messages:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append({**kwargs, "messages": list(kwargs.get("messages", []))})
        if not self._responses:
            raise AssertionError("FakeAnthropic ran out of scripted responses")
        return self._responses.pop(0)


class FakeAnthropic:
    def __init__(self, responses):
        self.messages = _Messages(responses)
