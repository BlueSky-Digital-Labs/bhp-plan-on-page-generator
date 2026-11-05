import logging
from typing import Any

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.access.errors import DependencyUnavailable
from app.access.service import AccessService
from app.access.wiring import build_access_service
from app.ai import ConverseStreamAdapter, build_bedrock_runtime_client, get_validated_bedrock_config
from app.ai.policy import Capability
from app.api import api_router
from app.api.access import dependency_unavailable
from app.appendix.summarizer import BedrockSummarizer
from app.config import Settings, get_settings
from app.conversation.dynamo_store import DynamoConversationStore
from app.conversation.producers import ScriptedTestProducer, UnavailableProducer
from app.conversation.service import ConversationService
from app.conversation.store import InMemoryConversationStore
from app.delivery import InMemoryDeliveryStore
from app.delivery.dynamo_store import DynamoDeliveryStore
from app.dynamo import build_client as build_dynamo_client
from app.errors import register_exception_handlers
from app.interview.dynamo_store import DynamoInterviewStore
from app.interview.engine import Interviewer
from app.interview.extractors import BedrockExtractor
from app.interview.producer import InterviewerProducer
from app.interview.store import InMemoryInterviewStore, InterviewStoreBase
from app.logging_config import configure_logging
from app.mailer import SesMailer, UnavailableMailer, build_ses_client
from app.mailer.service import MailService
from app.middleware import RequestContextMiddleware


class SpaStaticFiles(StaticFiles):
    """Serve the compiled app, falling back to index.html for client-side routes.

    Direct navigation or refresh on /start must load the React app (which then asks the backend
    whether access is allowed). Only extension-less, non-API paths fall back: a missing asset or
    an unknown /api path still returns the JSON 404 envelope. Protection is enforced by the API,
    not by which HTML is served.
    """

    async def get_response(self, path, scope):
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            leaf = path.rsplit("/", 1)[-1]
            if exc.status_code == 404 and not path.startswith("api") and "." not in leaf:
                return await super().get_response("index.html", scope)
            raise


def _build_stores(settings: Settings) -> tuple[Any, Any, InterviewStoreBase]:
    """(conversation store, delivery store, interview store).

    Deployed: all three live in the one DynamoDB table, so any task can serve any request and a
    delete, purge or expiry removes everything. Otherwise they are in this process's memory
    (local use and tests only).
    """
    if settings.is_deployed:
        client = build_dynamo_client(settings.aws_region)
        table = settings.sessions_table_name or ""  # required by Settings when deployed
        return (
            DynamoConversationStore(client, table),
            DynamoDeliveryStore(client, table),
            DynamoInterviewStore(client, table),
        )
    return InMemoryConversationStore(), InMemoryDeliveryStore(), InMemoryInterviewStore()


def build_conversation(settings: Settings) -> ConversationService:
    store, deliveries, interview_store = _build_stores(settings)
    choice = settings.conversation_producer
    if choice == "interviewer_bedrock":
        # Fails at startup (ConfigError) unless an approved Bedrock selection is configured.
        validated = get_validated_bedrock_config()
        adapter = ConverseStreamAdapter(build_bedrock_runtime_client(validated.config), validated)
        extractor = BedrockExtractor(
            adapter, force_tool=validated.supports(Capability.FORCED_TOOL_CHOICE)
        )
        producer = InterviewerProducer(Interviewer(extractor), interview_store)
        return ConversationService(
            store,
            producer,
            interview=interview_store,
            summarizer=BedrockSummarizer(adapter),
            deliveries=deliveries,
        )
    producer = (
        ScriptedTestProducer(delay_seconds=0.05) if choice == "test" else UnavailableProducer()
    )
    # Deployed, the interview store is always attached so a delete or the scheduled sweep (which
    # builds this without a producer) also removes the canvas drafts.
    return ConversationService(
        store,
        producer,
        deliveries=deliveries,
        interview=interview_store if settings.is_deployed else None,
    )


class DiscardingMailer:
    """TEST mode: accepts a message and drops it, so the screens can be exercised without AWS."""

    ready = True

    def send(self, email) -> None:
        return None


def _build_mail(settings: Settings) -> MailService:
    # Fails at startup (ConfigError, from Settings) when `ses` lacks a sender or allowed domains.
    domains = settings.allowed_email_domains
    if settings.email_delivery == "ses":
        client = build_ses_client(settings.ses_region or settings.aws_region)
        sender = settings.email_sender_address or ""
        return MailService(SesMailer(client, sender, settings.email_configuration_set), domains)
    if settings.email_delivery == "test":
        return MailService(DiscardingMailer(), domains)
    return MailService(UnavailableMailer(), domains)


def create_app(
    settings: Settings | None = None,
    *,
    access_service: AccessService | None = None,
    conversation_service: ConversationService | None = None,
    mail_service: MailService | None = None,
) -> FastAPI:
    """Build the app (run with `uvicorn --factory app.main:create_app`).

    Raises ConfigError before serving if required configuration is missing or invalid.
    """
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    app = FastAPI(
        title="Plan on a Page",
        # Interactive docs and the schema are for local work only.
        docs_url=None if settings.is_deployed else "/api/docs",
        redoc_url=None,
        openapi_url=None if settings.is_deployed else "/api/openapi.json",
    )
    app.dependency_overrides[get_settings] = lambda: settings
    app.add_middleware(RequestContextMiddleware)
    register_exception_handlers(app)
    app.add_exception_handler(DependencyUnavailable, dependency_unavailable)
    app.state.access = access_service or build_access_service(settings)
    app.state.conversation = conversation_service or build_conversation(settings)
    app.state.mail = mail_service or _build_mail(settings)
    app.include_router(api_router)

    # Same-origin serving of the compiled Vite build. Mounted last so /api wins.
    if (settings.static_dir / "index.html").is_file():
        app.mount("/", SpaStaticFiles(directory=settings.static_dir, html=True), name="frontend")
    else:
        logging.getLogger("app").info("frontend_build_absent")
    return app
