def handle(event):
    payload = event['payload']
    return payload.get('id')
