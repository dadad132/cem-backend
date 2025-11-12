from enum import StrEnum


class TaskStatus(StrEnum):
    todo = "todo"
    in_progress = "in_progress"
    done = "done"
    blocked = "blocked"


class TaskPriority(StrEnum):
    low = "low"
    medium = "medium"
    high = "high"
    critical = "critical"


class MeetingPlatform(StrEnum):
    zoom = "zoom"
    teams = "teams"
    discord = "discord"
    in_person = "in_person"
    google_meet = "google_meet"
    phone = "phone"
    other = "other"
