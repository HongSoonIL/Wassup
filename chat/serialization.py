from django.utils import timezone

def iso(dt):
    return timezone.localtime(dt).isoformat(timespec='seconds') if dt else None


def message_json(m):
    return {
        'id': m.id,
        'sender': m.sender,
        'kind': m.kind,
        'text': m.text,
        'status': m.status,
        'created_at': iso(m.created_at),
        'read_at': iso(m.read_at),
    }
