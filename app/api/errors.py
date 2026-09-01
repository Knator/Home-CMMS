"""Consistent JSON errors.

Every failure returns the same envelope so a client can rely on one shape:

    {"error": "Validation failed", "errors": {"asset_number": "..."}}
"""
from flask import jsonify


def error_response(status, message, errors=None):
    payload = {'error': message}
    if errors:
        payload['errors'] = errors
    return jsonify(payload), status


def bad_request(message='Validation failed', errors=None):
    return error_response(400, message, errors)


def unauthorized(message='A valid API token is required.'):
    response = jsonify({'error': message})
    # Tells a client how to authenticate rather than leaving it guessing.
    response.headers['WWW-Authenticate'] = 'Bearer realm="Home CMMS API"'
    return response, 401


def forbidden(message='This token does not permit that action.'):
    return error_response(403, message)


def not_found(message='Not found.'):
    return error_response(404, message)
