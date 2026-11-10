import json

from app.appendix.model import AppendixInput

FORMAT_NAME = "plan-on-a-page-appendix"
FORMAT_VERSION = 1


def render_json(appendix: AppendixInput) -> bytes:
    document = {
        "format": FORMAT_NAME,
        "version": FORMAT_VERSION,
        "source_revision": appendix.source_revision,
        "draft": appendix.draft,  # true: an unfinished canvas saved to carry on later
        "included_turns": appendix.included_turns,
        "summary": {
            "overview": appendix.summary.overview,  # AI-written from the conversation, or null
            "facts": [{"label": k, "text": v} for k, v in appendix.summary.facts],
            "key_answers": [{"label": k, "text": v} for k, v in appendix.summary.key_answers],
        },
        "sections": [
            {
                "title": section.title,
                "answers": [
                    {
                        "id": a.field_id,
                        "label": a.label,
                        "status": a.status,
                        "value": list(a.value) if isinstance(a.value, tuple) else a.value,
                    }
                    for a in section.answers
                ],
            }
            for section in appendix.sections
        ],
        "conversation": [
            {"turn": t.number, "speaker": t.speaker, "text": t.text} for t in appendix.turns
        ],
    }
    return json.dumps(document, ensure_ascii=False, indent=2).encode("utf-8")
