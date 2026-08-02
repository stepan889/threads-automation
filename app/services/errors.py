class ServiceError(Exception):
    """Safe integration error suitable for showing to an administrator."""


class EmptyGenerationError(ServiceError):
    """The text-generation provider completed without usable post text."""
