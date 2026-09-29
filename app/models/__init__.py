"""
Registers every model with Base.metadata so that
`import app.models` in main.py makes create_all() see all tables.
"""
from app.models.user import User, UserRole, Department, WorkerLevel                 # noqa: F401
from app.models.ticket import Ticket, TicketStatus, TicketPriority, Terminal        # noqa: F401
from app.models.audit_log import AuditLog, AuditAction                              # noqa: F401
from app.models.registration_code import RegistrationCode                           # noqa: F401
from app.models.help_request import HelpRequest, HelpRequestStatus                  # noqa: F401
from app.models.hold_request import HoldRequest, HoldRequestStatus                  # noqa: F401
