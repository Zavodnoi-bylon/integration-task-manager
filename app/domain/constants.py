STATUS_OPEN = "В работе"
STATUS_COMPLETED = "Завершена"

TICKET_TYPE_GENERAL = "general"
TICKET_TYPE_DESTINATION = "destination"
TICKET_TYPES = frozenset({TICKET_TYPE_GENERAL, TICKET_TYPE_DESTINATION})

ROLE_CLIENT = "client"
ROLE_TECHNICIAN = "technician"
ROLE_ADMIN = "admin"

EVENT_CREATED = "created"
EVENT_COMMENT = "comment"
EVENT_PUBLIC_MESSAGE = "public_message"
EVENT_STATUS_CHANGED = "status_changed"
