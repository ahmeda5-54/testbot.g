from aiogram.fsm.state import State, StatesGroup


class VerifyState(StatesGroup):
    account = State()
    broker = State()
    server_choice = State()
    server = State()
    photo = State()


class SupportState(StatesGroup):
    awaiting = State()
