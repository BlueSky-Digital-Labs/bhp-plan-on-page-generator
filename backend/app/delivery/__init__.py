"""Which documents were delivered, and whether the session may be deleted by the user.

The server records when it finishes sending each PDF. That is the only fact recorded: it is not
proof that the browser received the file or that the user saved it. See docs/downloads.md.
"""

from app.delivery.gate import PurgeGate, evaluate_gate
from app.delivery.store import (
    DOCUMENT_KINDS,
    Delivery,
    DeliveryStore,
    DocumentKind,
    InMemoryDeliveryStore,
    UnavailableDeliveryStore,
)

__all__ = [
    "DOCUMENT_KINDS",
    "Delivery",
    "DeliveryStore",
    "DocumentKind",
    "InMemoryDeliveryStore",
    "PurgeGate",
    "UnavailableDeliveryStore",
    "evaluate_gate",
]
