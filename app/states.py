from aiogram.fsm.state import State, StatesGroup


class UserStates(StatesGroup):
    choosing_ticket_type = State()
    waiting_for_ticket_message = State()
    waiting_for_destination_advertiser = State()
    waiting_for_destination_custom_advertiser = State()
    waiting_for_destination_cap = State()
    waiting_for_destination_offer = State()
    waiting_for_ticket_comment = State()
    waiting_for_ticket_public_message = State()
