"""Finite-state groups for booking and doctor setup."""

from aiogram.fsm.state import State, StatesGroup


class BookingStates(StatesGroup):
    """Patient's current booking step."""

    choosing_doctor = State()
    choosing_date = State()
    choosing_slot = State()
    visit_reason = State()


class PatientProfileStates(StatesGroup):
    """Patient name, phone and consent steps."""

    full_name = State()
    phone = State()
    consent = State()


class DoctorSetupStates(StatesGroup):
    """Admin's guided doctor and weekly schedule setup."""

    doctor_details = State()
    schedule_lines = State()
    edit_name = State()
    edit_specialty = State()
    edit_schedule = State()


class AdminDayStates(StatesGroup):
    """Admin prompt for the reason behind closing a clinic day."""

    closure_reason = State()
