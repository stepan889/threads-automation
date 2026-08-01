import logging


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=level.upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    # httpx INFO messages include complete request URLs. Threads accepts tokens
    # as query parameters in some flows, so never allow those URLs into logs.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
