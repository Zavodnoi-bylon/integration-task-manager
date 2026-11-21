from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt

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


class DestinationResultOutput(StrictOutput):
    naming: str = Field(min_length=1, max_length=512)
    url: str = Field(min_length=1, max_length=16_384)


class TicketSummary(StrictOutput):
    ticket_id: int = Field(gt=0)
    ticket_number: int = Field(gt=0)
    profile_name: str = Field(min_length=1, max_length=128)
    status: Literal["open", "review", "completed"]
    ticket_type: Literal["general", "destination"]
    title: str = Field(min_length=1, max_length=256)
    description_preview: str = Field(max_length=512)
    created_at: datetime
    updated_at: datetime
    closed_at: datetime | None = None
    issued_to_buying: bool = False
    important_comment: str | None = Field(default=None, max_length=2_000)
    destination_results: list[DestinationResultOutput] = Field(default_factory=list)


class ClientTicketDetail(TicketSummary):
    description: str
    is_author: bool
    conversation: list[PublicConversationMessage]


class StaffTicketDetail(ClientTicketDetail):
    new_advertiser: bool
    internal_history: list[InternalConversationMessage]


class AdvertiserSummary(StrictOutput):
    key: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=256)


class AdvertiserRefreshStatus(StrictOutput):
    status: Literal["idle", "pending", "running", "succeeded", "failed"]


class PostbackSummary(StrictOutput):
    key: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=256)


class PostbackDetail(PostbackSummary):
    template: str = Field(min_length=1, max_length=16_384)


class HelpContent(StrictOutput):
    audience: Literal["client", "staff"]
    markdown: str = Field(min_length=1, max_length=65_536)


class CatalogAdvertiserInput(StrictInput):
    kind: Literal["catalog"]
    key: str = Field(min_length=1, max_length=128)


class NewAdvertiserInput(StrictInput):
    kind: Literal["new"]
    name: str = Field(min_length=1, max_length=256)


AdvertiserInput = Annotated[
    CatalogAdvertiserInput | NewAdvertiserInput, Field(discriminator="kind")
]

LimitedCap = Annotated[StrictInt, Field(gt=0, le=1_000_000)]
CapValue = LimitedCap | Literal["uncap"]
CapMode = Literal["unlim", "daily", "monthly", "lifetime"]


class KalistoDestinationDetails(StrictInput):
    naming: str = Field(min_length=1, max_length=512)
    offer_name: str = Field(min_length=1, max_length=256)
    platform: str = Field(min_length=1, max_length=128)
    approach: str = Field(min_length=1, max_length=256)
    geo: str = Field(min_length=1, max_length=128)
    rate: Decimal = Field(gt=0, max_digits=18, decimal_places=4)
    currency: str = Field(min_length=1, max_length=32)
    landing_url: str = Field(min_length=1, max_length=16_384)
    clickid_subid: str = Field(default="", max_length=16_384)
    additional: str = Field(default="", max_length=16_384)


class KalistoDestinationDraftDetails(StrictInput):
    naming: str | None = Field(default=None, min_length=1, max_length=512)
    offer_name: str | None = Field(default=None, min_length=1, max_length=256)
    platform: str | None = Field(default=None, min_length=1, max_length=128)
    approach: str | None = Field(default=None, min_length=1, max_length=256)
    geo: str | None = Field(default=None, min_length=1, max_length=128)
    rate: Decimal | None = Field(
        default=None, gt=0, max_digits=18, decimal_places=4
    )
    currency: str | None = Field(default=None, min_length=1, max_length=32)
    landing_url: str | None = Field(default=None, min_length=1, max_length=16_384)
    clickid_subid: str = Field(default="", max_length=16_384)
    additional: str = Field(default="", max_length=16_384)


class GeneralTicketCreate(StrictInput):
    type: Literal["general"]
    body: str = Field(min_length=1, max_length=16_384)


class DestinationTicketCreate(StrictInput):
    type: Literal["destination"]
    advertiser: AdvertiserInput
    cap: CapValue
    cap_mode: CapMode
    body: str = Field(default="", max_length=16_384)
    important_comment: str = Field(default="", max_length=2_000)
    details: KalistoDestinationDetails | None = None


class TicketDraftSummary(StrictOutput):
    draft_id: int = Field(gt=0)
    source_preview: str = Field(min_length=1, max_length=512)
    client_comment: str = Field(max_length=16_384)
    created_at: datetime
    updated_at: datetime


class TicketDraftDetail(TicketDraftSummary):
    source_body: str = Field(min_length=1, max_length=16_384)
    edited_body: str = Field(min_length=1, max_length=16_384)
    advertiser_kind: Literal["catalog", "new"] | None = None
    advertiser_key: str | None = Field(default=None, max_length=128)
    advertiser_name: str | None = Field(default=None, max_length=256)
    cap: CapValue | None = None
    cap_mode: CapMode | None = None
    forward_origin_name: str | None = Field(default=None, max_length=256)
    ticket_type: Literal["general", "destination"] | None = None
    important_comment: str = Field(default="", max_length=2_000)
    details: KalistoDestinationDraftDetails | None = None


class DraftSaveInput(StrictInput):
    type: Literal["general", "destination"] | None = None
    advertiser: AdvertiserInput | None = None
    cap: CapValue | None = None
    cap_mode: CapMode | None = None
    body: str = Field(min_length=1, max_length=16_384)
    client_comment: str = Field(default="", max_length=16_384)
    important_comment: str = Field(default="", max_length=2_000)
    details: KalistoDestinationDraftDetails | None = None


class GeneralDraftSubmitInput(StrictInput):
    type: Literal["general"]
    body: str = Field(min_length=1, max_length=16_384)
    client_comment: str = Field(default="", max_length=16_384)


class DestinationDraftSubmitInput(StrictInput):
    type: Literal["destination"]
    advertiser: AdvertiserInput
    cap: CapValue
    cap_mode: CapMode
    body: str = Field(min_length=1, max_length=16_384)
    client_comment: str = Field(default="", max_length=16_384)
    important_comment: str = Field(default="", max_length=2_000)
    details: KalistoDestinationDetails | None = None


DraftSubmitInput = Annotated[
    GeneralDraftSubmitInput | DestinationDraftSubmitInput,
    Field(discriminator="type"),
]


class DraftMutationResponse(StrictOutput):
    draft_id: int = Field(gt=0)
    status: Literal["submitted"]
    ticket_id: int = Field(gt=0)
    ticket_number: int | None = Field(default=None, gt=0)
    replayed: bool


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


class BuyingFlagInput(StrictInput):
    issued: StrictBool


class BuyingFlagResponse(StrictOutput):
    ticket_id: int = Field(gt=0)
    issued_to_buying: bool


class MutationResponse(StrictOutput):
    ticket_id: int = Field(gt=0)
    ticket_number: int | None = Field(default=None, gt=0)
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
