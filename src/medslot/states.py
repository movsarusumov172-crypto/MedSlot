"""Finite-state groups for booking and doctor setup."""

from aiogram.fsm.state import State, StatesGroup


class BookingStates(StatesGroup):
    """Patient's current booking step."""

    choosing_doctor = State()
    choosing_date = State()
    choosing_slot = State()


class DoctorSetupStates(StatesGroup):
    """Admin's guided doctor and weekly schedule setup."""

    doctor_details = State()
    schedule_lines = State()
