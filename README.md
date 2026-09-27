# Maasai Park — Modern Parking System

A functional, web-based parking management prototype built for the
"modern parking system" brief: drivers see live slot availability,
vehicles are recorded on arrival, and on exit the system calculates
duration + fee automatically before the barrier opens.

Stack: **Python 3 + Flask** (web layer) + **SQLite** (dynamic database).
Chosen because it needs no external DB server to install for a class
submission, while still being a real relational database with proper
tables, keys and growth — not a flat file.

---

## 1. Use cases identified

| Actor            | Use case                                              |
|-------------------|--------------------------------------------------------|
| Driver             | View available slots before entering                  |
| Gate attendant/system | Record vehicle arrival, assign a slot                |
| Driver / attendant | Request exit, view fee owed                            |
| System              | Calculate fee based on duration                        |
| Driver / attendant | Confirm payment                                          |
| Barrier            | Open on successful payment                              |
| Manager            | View history/log of all sessions                        |

## 2. Modules proposed

1. **Slot Management Module** — tracks every slot's state (available /
   occupied) and drives the visual display.
2. **Vehicle Entry Module** — validates and records an arriving vehicle,
   allocates it a slot.
3. **Fee Calculation Module** — pure function that turns
   (entry_time, exit_time) into a fee using the client's tiers.
4. **Vehicle Exit & Payment Module** — looks up the active session,
   shows the fee, and finalises the session on payment.
5. **Barrier Control Module** — simulated: opens (returns `"OPEN"`)
   only after payment is confirmed. On real hardware this would send a
   signal to a relay/controller instead of returning a string.

All of these live in `algorithms.py` (the algorithms) and `app.py`
(the web routes that expose them), with clear docstrings explaining
each step.

## 3. Algorithms (see `algorithms.py` for full comments)

- **Slot allocation**: pop a free slot number from the front of a
  FIFO queue — O(1) — rather than scanning every slot for the first
  free one (O(n)). Freed slots are pushed to the back of the queue,
  spreading wear evenly.
- **Fee calculation**: tiered decision logic matching the client's
  exact bands (free ≤30 min, Kshs 50 ≤2h, Kshs 100 ≤4h, Kshs 300 ≤6h,
  Kshs 500 beyond) — O(1).
- **Session lookup on exit**: hash map keyed by plate number gives
  O(1) lookup instead of scanning the sessions log.

## 4. Data structures and why

| Structure          | Used for                          | Why this one |
|---------------------|-------------------------------------|----------------|
| `list` (array)       | Ordered slots for the visual grid  | Matches the physical, numbered layout; O(1) indexed access for rendering |
| `collections.deque` (queue) | Available slot numbers      | O(1) allocate (`popleft`) and release (`append`); FIFO fairness across bays |
| `dict` (hash map)     | Active sessions keyed by plate     | O(1) lookup on exit instead of O(n) scan of the sessions log |
| SQLite tables         | Durable state + full history       | Survives restarts; supports reporting/history; relational integrity via foreign key |

## 5. Dynamic database design

```
slots
-----
id            INTEGER PRIMARY KEY
slot_number   TEXT UNIQUE     -- e.g. "S01"
status        TEXT            -- 'available' | 'occupied'

sessions
--------
id            INTEGER PRIMARY KEY
plate_number  TEXT
slot_id       INTEGER  -> FK slots.id
entry_time    TEXT (ISO datetime)
exit_time     TEXT (ISO datetime, NULL while active)
fee           REAL (NULL until paid)
status        TEXT            -- 'active' | 'completed'
```

Why it's "dynamic": `sessions` is append-only and grows with every
car that passes through (this is your history/audit trail for free).
`slots` can be scaled up at any time by changing `TOTAL_SLOTS` in
`database.py` and calling `ensure_slot_count()` — existing data is
never touched, only new rows are added.

## 6. How to run it

```bash
cd parking_system
python3 -m venv venv
source venv/bin/activate        # on Windows: venv\Scripts\activate
pip install -r requirements.txt
python3 app.py
```

Then open **http://localhost:5000** in your browser.

- The dashboard shows the live slot grid (green = available, red =
  occupied) and a form to record an arrival.
- "Exit / Pay" looks up a plate, shows the fee owed, and on
  "Confirm Payment" opens the barrier and shows a receipt.
- "History" lists the last 100 sessions from the database.

The database file `parking.db` is created automatically on first run
in the project folder — nothing else to configure.

## Staff login

The dashboard, vehicle entry, checkout, payment status, and session
history require staff sign-in. Open the app and sign in with the demo
account `staff` / `Maasai@35`. To use different credentials, set
`STAFF_USERNAME` and `STAFF_PASSWORD` in the application environment.
Set a private random `SECRET_KEY` before deployment so login sessions
cannot be forged. Sign out with the button in the page header.

## M-Pesa STK Push

Checkout sends a Daraja M-Pesa Express (STK Push) prompt to the phone
number entered by the customer. A parking session is completed and its
bay released only after a successful payment callback is received.

1. Create a Daraja app and configure its credentials for the sandbox or
   production account.
2. Set the variables shown in `.env.example` in the application
   environment. Do not commit real credentials.
3. Set `MPESA_CALLBACK_URL` to the publicly reachable HTTPS URL ending
   in `/mpesa/callback`; Daraja must be able to reach it.
4. Run the app and use an authorized Daraja test number while in sandbox.

The sample uses `CustomerPayBillOnline`. Set
`MPESA_TRANSACTION_TYPE=CustomerBuyGoodsOnline` for a Till integration.
The Daraja account must be approved and configured for the chosen
transaction type before switching `MPESA_ENVIRONMENT` to `production`.

## 7. Pushing to GitHub

```bash
git init
git add .
git commit -m "Modern Parking System prototype"
git branch -M main
git remote add origin <your-repo-url>
git push -u origin main
```

(`.gitignore` already excludes `parking.db`, `venv/` and `__pycache__/`.)

## 8. What to extend next

- **Authentication** for attendants/managers before they can view history.
- **Number-plate recognition (ANPR)** camera integration instead of
  manual plate entry, feeding straight into `park_vehicle()`.
- **Reserved/VIP slots**: add a `slot_type` column and a second queue
  so reserved bays aren't handed out by the general FIFO.
- **Real barrier hardware**: replace the `"barrier": "OPEN"` string in
  `checkout_vehicle()` with a GPIO/serial signal to an actual relay.
- **M-Pesa/online payment** integration in the checkout confirm step,
  instead of the current "assume paid on confirm" flow.
- **Multi-level/multi-branch support**: add a `level` or `branch_id`
  column to `slots` and partition the queue per level.
