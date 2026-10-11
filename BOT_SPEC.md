# MedSlot — project specification

## Idea and profile

Telegram appointment booking for clinic patients and clinic admins. Patients choose an
available doctor appointment and manage active bookings. Admins maintain doctors and schedules.

| Dimension | Decision |
|---|---|
| Audience / access | Public patient chat; admin actions restricted to numeric `ADMIN_IDS`. |
| Surface | Private Telegram chat. |
| Operator | Clinic admins. |
| Money | None. |
| User content | No free-form health information is collected. |
| Time | Weekly doctor schedules, closures, 24-hour and 2-hour reminders. |
| Data sensitivity | Booking time and Telegram ID are stored; no diagnoses or documents. |
| Scale | One bot process with SQLite. |
| Language | Russian patient and admin interface. |
| Tenancy | One clinic. |

## Roles and stories

- Patient: choose specialty, doctor, date, and an available slot; see active appointments; cancel
  an owned appointment no later than two hours before it; receive two advance reminders.
- Admin: create a doctor and weekly work intervals; inspect one doctor's bookings for a date;
  close a doctor's day for leave or illness and notify affected patients.

## Data and invariants

- `Doctor`: name, specialty, active flag.
- `WorkSchedule`: doctor, weekday, local start/end, appointment duration. Same-day intervals may
  touch but may not overlap.
- `DayClosure`: doctor and local date are unique; includes a short reason.
- `Appointment`: patient Telegram ID, doctor, UTC start, status, reminder flags, creation time.
  A partial unique index allows at most one active appointment per doctor and start time.
- Cancelled and closed appointments remain for history; a cancelled slot becomes available again.

## Decisions and assumptions

- Clinic timezone is configurable and defaults to `Europe/Tallinn` based on the current project
  environment; admins should change it if the clinic is elsewhere.
- An admin closes a date for one doctor at a time. `/doctors` provides the ID used by `/day` and
  `/close_day`.
- The admin enters schedule weekdays as ISO-like numbers `1=Monday` to `7=Sunday` and may enter
  multiple non-overlapping intervals per weekday.
- A closed day's active appointments change to `closed`, and each affected patient is sent a
  cancellation notice with the admin's reason.
- FSM state is in memory and is lost on restart; committed doctors and bookings are persistent.
- A reminder is not sent if a booking is created after that reminder's advance point.
- Database tables are created on startup; versioned migrations are deferred until schema changes
  need to be applied to an existing deployment.

## Build progress

- [x] Project layout, configuration, data models, and Docker/CI files.
- [x] Slot generation, schedule validation, booking and cancellation rules.
- [x] Patient and admin Telegram flows.
- [x] APScheduler reminder scan and day closure notifications.
- [x] Focused tests and English README.
- [x] Python source parsing and Telegram-specific static audit (0 findings).
- [ ] Dependency installation and runtime verification (requires permission to download Python
  packages; Docker CLI is not available in the current environment).
