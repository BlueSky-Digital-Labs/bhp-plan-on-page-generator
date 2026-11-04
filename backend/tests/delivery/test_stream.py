"""What counts as delivered: the last chunk handed over, and nothing earlier."""

import logging

import pytest

from app.api.exports import CHUNK_BYTES, _stream, delivery_recorder


def test_the_file_is_sent_in_chunks_and_unchanged():
    content = bytes(range(256)) * 1000  # more than one chunk
    assert len(content) > CHUNK_BYTES
    chunks = list(_stream(content, None))
    assert b"".join(chunks) == content and all(len(c) <= CHUNK_BYTES for c in chunks)


def test_delivery_is_noted_only_after_every_chunk_has_been_handed_over():
    events = []
    content = b"x" * (CHUNK_BYTES * 2 + 5)
    for chunk in _stream(content, lambda: events.append("delivered")):
        events.append(len(chunk))
    assert events == [CHUNK_BYTES, CHUNK_BYTES, 5, "delivered"]


def test_nothing_is_noted_before_the_first_chunk_is_requested():
    events = []
    stream = _stream(b"abc", lambda: events.append("delivered"))  # headers could go out here
    assert events == []
    assert (
        next(stream) == b"abc" and events == []
    )  # the last chunk is out, the stream not yet ended
    with pytest.raises(StopIteration):
        next(stream)
    assert events == ["delivered"]


def test_a_cancelled_or_dropped_stream_notes_nothing():
    events = []
    stream = _stream(b"x" * (CHUNK_BYTES * 3), lambda: events.append("delivered"))
    next(stream)  # the client takes one chunk, then disconnects
    stream.close()
    assert events == []


def test_an_empty_file_is_delivered_when_the_stream_ends():
    events = []
    assert list(_stream(b"", lambda: events.append("delivered"))) == []
    assert events == ["delivered"]


def test_a_failure_while_noting_delivery_never_breaks_the_download_and_is_logged_plainly():
    records: list[logging.LogRecord] = []

    class Collect(logging.Handler):
        def emit(self, record):
            records.append(record)

    def broken():
        raise RuntimeError("SECRET-IDEA-TEXT")

    logger = logging.getLogger("app.downloads")
    handler = Collect()
    logger.addHandler(handler)
    try:
        assert b"".join(_stream(b"abc", broken)) == b"abc"
    finally:
        logger.removeHandler(handler)
    assert [r.getMessage() for r in records] == ["delivery_record_failed"]
    assert "SECRET" not in repr([r.__dict__ for r in records])


def test_only_the_pdf_of_each_document_is_noted():
    calls = []

    class Service:
        def record_delivery(self, session, kind, revision):
            calls.append((kind, revision))

    class Snapshot:
        revision = 7

    for fmt in ("pptx", "docx", "json"):
        assert delivery_recorder(Service(), None, "canvas", Snapshot(), fmt) is None
    recorder = delivery_recorder(Service(), None, "appendix", Snapshot(), "pdf")
    recorder()
    assert calls == [("appendix", 7)]
