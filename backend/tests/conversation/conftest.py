import json

from tests.access.conftest import (
    csrf,
    signed_in,
    world,  # noqa: F401  (re-exported fixture)
)


class ListProducer:
    """Yields fixed chunks and counts how many times it was asked to produce."""

    def __init__(self, chunks):
        self.chunks, self.calls = list(chunks), 0

    def stream(self, user_text):
        self.calls += 1
        yield from self.chunks


class RaisingProducer:
    def __init__(self, exc):
        self.exc = exc

    def stream(self, user_text):
        yield "partial "
        raise self.exc


def use_producer(w, producer):
    w.conversation._producer = producer
    return producer


def submit(client, text="My idea", revision=0, submission_id="sub-00000001", **kw):
    body = {"submission_id": submission_id, "expected_revision": revision, "text": text}
    return client.post("/api/conversation/turns", json=body, headers=csrf(client), **kw)


def events(response):
    return [json.loads(line) for line in response.text.splitlines()]


def browser(w):
    return signed_in(w)
