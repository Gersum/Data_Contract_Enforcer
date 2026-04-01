def emit_event(event_type: str, payload: dict) -> dict:
    return {"event_type": event_type, "payload": payload}
