"""The mail settings the routes need, in one place: who sends, who may receive, how often."""

from dataclasses import dataclass, field

from app.mailer.limits import SendLimiter
from app.mailer.message import Mailer, UnavailableMailer


@dataclass
class MailService:
    mailer: Mailer = field(default_factory=UnavailableMailer)
    allowed_domains: tuple[str, ...] = ()
    limiter: SendLimiter = field(default_factory=SendLimiter)
