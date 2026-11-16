"""
customer.created / customer.updated / customer.imported -> User

Docs: https://developer.paddle.com/webhooks/customers/customer-updated
"""
from datetime import datetime
from typing import Annotated

from pydantic import AfterValidator, StringConstraints

from schemas.base import CustomerId, PaddleModel, PaddleWebhook, Status

# Trimmed and lowercased: User.email is unique, "Jo@Example.com" and "jo@example.com" are one user
Email = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=3, max_length=1024, pattern=r"^[^@\s]+@[^@\s]+$"),
    AfterValidator(str.lower),
]


class Customer(PaddleModel):
    id: CustomerId  # -> User.paddle_customer_id
    email: Email
    name: str | None
    marketing_consent: bool
    status: Status  # -> User.archived (mirror only: the customer's licenses stay valid)
    updated_at: datetime  # -> User.updated_at: older webhooks are skipped (out-of-order delivery)


CustomerWebhook = PaddleWebhook[Customer]
