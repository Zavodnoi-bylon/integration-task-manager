from aiogram.fsm.state import State, StatesGroup


class UserStates(StatesGroup):
    waiting_for_ticket_message = State()
    waiting_for_ticket_comment = State()
    waiting_for_ticket_public_message = State()
