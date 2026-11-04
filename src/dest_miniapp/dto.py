from __future__ import annotations

from datetime import datetime
from typing import Annotated, Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field

from .models import Role


class StrictOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class StrictInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ActorPublic(StrictOutput):
    display_name: str = Field(min_length=1, max_length=128)
    role: Role


class PublicConversationMessage(StrictOutput):
    message_id: int = Field(gt=0)
    visibility: Literal["public"]
    body: str
    sent_at: datetime
    actor: ActorPublic


class InternalConversationMessage(StrictOutput):
    message_id: int = Field(gt=0)
    visibility: Literal["internal"]
    body: str
    sent_at: datetime
    actor: ActorPublic


class TicketSummary(StrictOutput):
    ticket_id: int = Field(gt=0)
    profile_name: str = Field(min_length=1, max_length=128)
    status: Literal["open", "review", "completed"]
    ticket_type: Literal["general", "destination"]
    title: str = Field(min_length=1, max_length=256)
    description_preview: str = Field(max_length=512)
    created_at: datetime
    updated_at: datetime
    closed_at: datetime | None = None


class ClientTicketDetail(TicketSummary):
    description: str
    conversation: list[PublicConversationMessage]


class StaffTicketDetail(ClientTicketDetail):
    internal_history: list[InternalConversationMessage]


class AdvertiserSummary(StrictOutput):
    key: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=256)


class PostbackSummary(StrictOutput):
    key: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=256)


class PostbackDetail(PostbackSummary):
    template: str = Field(min_length=1, max_length=16_384)


class CatalogAdvertiserInput(StrictInput):
    kind: Literal["catalog"]
    key: str = Field(min_length=1, max_length=128)


class NewAdvertiserInput(StrictInput):
    kind: Literal["new"]
    name: str = Field(min_length=1, max_length=256)


AdvertiserInput = Annotated[
    CatalogAdvertiserInput | NewAdvertiserInput, Field(discriminator="kind")
]


class GeneralTicketCreate(StrictInput):
    type: Literal["general"]
    body: str = Field(min_length=1, max_length=16_384)


class DestinationTicketCreate(StrictInput):
    type: Literal["destination"]
    advertiser: AdvertiserInput
    cap: int = Field(gt=0, le=1_000_000)
    body: str = Field(min_length=1, max_length=16_384)


CreateTicketInput = Annotated[
    GeneralTicketCreate | DestinationTicketCreate, Field(discriminator="type")
]


class MessageInput(StrictInput):
    visibility: Literal["public", "internal"]
    body: str = Field(min_length=1, max_length=16_384)


class ReasonInput(StrictInput):
    reason: str = Field(min_length=1, max_length=16_384)


class EmptyInput(StrictInput):
    pass


class MutationResponse(StrictOutput):
    ticket_id: int = Field(gt=0)
    status: Literal["open", "review", "completed"]
    ticket_type: Literal["general", "destination"]
    review_cycle: int = Field(ge=0)
    replayed: bool


ItemT = TypeVar("ItemT")


class Page(StrictOutput, Generic[ItemT]):
    items: list[ItemT]
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)
    has_next: bool
