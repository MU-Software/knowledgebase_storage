from __future__ import annotations


class DomainError(Exception):
    pass


class ResourceNotFoundError(DomainError):
    def __init__(self, resource: str) -> None:
        self.resource = resource
        super().__init__(f"{resource} not found")


class StaleClaimError(DomainError):
    def __init__(self, job_id: object) -> None:
        super().__init__(f"job {job_id} is no longer held by this claim")


class ReduceNotConvergingError(DomainError):
    def __init__(self, remaining: int) -> None:
        super().__init__(f"summary did not fit the context budget after repeated reduction ({remaining} chunks left)")


class ContextTooSmallError(DomainError):
    def __init__(self, context_tokens: int, minimum: int) -> None:
        super().__init__(f"context_tokens {context_tokens} leaves no room for the prompt; needs at least {minimum}")
