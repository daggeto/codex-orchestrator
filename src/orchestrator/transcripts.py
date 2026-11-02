"""Public representation of turns that consume private recovery evidence."""

PRIVATE_ASSESSMENT = 'Recovery assessment used private checkout evidence. See the incident report for its result.'


def private_assessment_payload(kind, payload):
    # Backend echoes, tool output and reasoning can repeat the input verbatim.
    # Keep event identity/cursors outside this payload and expose only lifecycle.
    result = {'private_assessment': True}
    if kind == 'user':
        result['text'] = PRIVATE_ASSESSMENT
    elif kind == 'error':
        result['message'] = 'Private recovery assessment failed. See the incident report.'
    elif kind == 'turn/ended':
        result['completed'] = payload.get('completed') is True
        if payload.get('error'):
            result['error'] = 'Private recovery assessment did not complete.'
    elif kind == 'turn/completed':
        turn = payload.get('turn')
        result['is_error'] = bool(payload.get('is_error') or isinstance(turn, dict)
                                  and (turn.get('error') or turn.get('status') == 'failed'))
    return result
