"""The mail settings the routes need, in one place: who sends, who receives, how often."""

from dataclasses import dataclass, field

from app.mailer.limits import SendLimiter
from app.mailer.message import Mailer, UnavailableMailer
from app.mailer.settings import NoSubmitSettings, SubmitSettingsSource


@dataclass
class MailService:
    mailer: Mailer = field(default_factory=UnavailableMailer)
    submit: SubmitSettingsSource = field(default_factory=NoSubmitSettings)
    limiter: SendLimiter = field(default_factory=SendLimiter)
