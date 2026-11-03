from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
def health() -> dict[str, str]:
    """Liveness for the ALB target group and the container HEALTHCHECK."""
    return {"status": "ok"}
