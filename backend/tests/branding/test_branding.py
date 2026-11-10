"""The app name and header text come from configuration and reach the page, the appendix and the email."""

import pytest
from pydantic import ValidationError

from app.config import AppEnv, Settings, failed_fields
from tests.appendix.test_appendix_api import appendix, finish_confirmed
from tests.interview.conftest import browser
from tests.interview.test_interview_api import wire


def settings(tmp_path, **kwargs) -> Settings:
    (tmp_path / "index.html").write_text("<!doctype html>")
    return Settings(app_env=AppEnv.LOCAL, static_dir=tmp_path, **kwargs)


def test_the_defaults_keep_todays_wording(tmp_path):
    s = settings(tmp_path)
    assert (s.app_name, s.header_text, s.footer_text) == (
        "SCP AI Ideation assistant",
        "SCP AI Ideation assistant",
        "BHP",
    )


def test_the_names_can_be_set_without_a_rebuild_and_are_tidied(tmp_path):
    s = settings(
        tmp_path,
        app_name="  Integrated Operations \n Plan on a Page ",
        header_text="Integrated Operations",
        footer_text="BHP · Integrated Operations",
    )
    assert s.app_name == "Integrated Operations Plan on a Page"
    assert (
        s.header_text == "Integrated Operations" and s.footer_text == "BHP · Integrated Operations"
    )


@pytest.mark.parametrize("bad", ["", "   ", "x" * 81, "bad\x00name"])
@pytest.mark.parametrize("field", ["app_name", "header_text", "footer_text"])
def test_empty_overlong_or_control_text_stops_startup_naming_only_the_setting(tmp_path, field, bad):
    with pytest.raises(ValidationError) as caught:
        settings(tmp_path, **{field: bad})
    assert failed_fields(caught.value) == field


def test_the_page_reads_the_names_from_the_server_with_no_session_needed(world):
    client = world.client()  # no sign-in: the entry page needs these too
    response = client.get("/api/branding")
    assert response.status_code == 200
    assert response.json() == {
        "app_name": "SCP AI Ideation assistant",
        "header_text": "SCP AI Ideation assistant",
        "footer_text": "BHP",
    }
    assert response.headers["cache-control"] == "no-store"  # never cached: a change shows at once


def test_a_configured_name_is_what_the_page_is_told(world):
    world._settings = world._settings.model_copy(
        update={"app_name": "Integrated Operations", "header_text": "IO"}
    )
    body = world.client().get("/api/branding").json()
    assert body["app_name"] == "Integrated Operations" and body["header_text"] == "IO"


def test_the_appendix_carries_the_configured_name(world):
    wire(world)
    world._settings = world._settings.model_copy(update={"app_name": "Integrated Operations"})
    client = browser(world)
    finish_confirmed(client)
    docx = appendix(client, "docx")
    assert docx.status_code == 200
    import io
    import zipfile

    xml = zipfile.ZipFile(io.BytesIO(docx.content)).read("word/document.xml").decode()
    assert (
        "Integrated Operations: Appendix" in xml
        and "SCP AI Ideation assistant: Appendix" not in xml
    )
    pdf = appendix(client, "pdf")
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
