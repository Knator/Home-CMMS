"""API documentation, defined once and rendered two ways.

Everything below drives both the OpenAPI document at /api/v1/openapi.json and
the human page at /api/v1/docs, so the two can never disagree. A test asserts
that every registered /api route appears here, which is what stops the docs
drifting away from the code as endpoints are added.
"""
from app.models.mixins import LIFECYCLE_STATUSES
from app.models.work_order import WO_PRIORITIES, WO_STATUSES, WO_TYPES

API_TITLE = 'Home CMMS API'
API_VERSION = '1.0.0'
API_DESCRIPTION = (
    'Create and read work orders from scripts and home-automation tools.\n\n'
    'Assets and locations are referenced by their **number** (`AST-00001`, '
    '`LOC-00003`), never by name — asset names are not unique, and location '
    'names are only unique within a parent, so a name is not something a client '
    'can reliably address.'
)

AUTH_DESCRIPTION = (
    'Every request needs a token, issued from Admin → Users → (a user) → API Access. '
    'Name each token after whatever will use it, so one integration can be revoked '
    'without disturbing the others. Send it as either header:\n\n'
    '    Authorization: Bearer YOUR_TOKEN\n'
    '    X-API-Key: YOUR_TOKEN'
)

WORK_ORDER_FIELDS = [
    ('title', 'string', True, 'Short summary. Required, 200 characters maximum.'),
    ('asset_number', 'string', False, 'Asset this work is against, e.g. `AST-00001`.'),
    ('location_number', 'string', False,
     'Where the work happens, e.g. `LOC-00003`. Taken from the asset when omitted.'),
    ('type', 'string', False, f"One of: {', '.join(WO_TYPES)}. Defaults to `unplanned`."),
    ('status', 'string', False, f"One of: {', '.join(WO_STATUSES)}. Defaults to `open`."),
    ('priority', 'string', False, f"One of: {', '.join(WO_PRIORITIES)}. Defaults to `medium`."),
    ('due_date', 'string', False, 'Date as `YYYY-MM-DD`.'),
    ('completed_date', 'string', False,
     'Date as `YYYY-MM-DD`. Set automatically to today when status is `completed`.'),
    ('overdue_grace_days', 'integer', False,
     'Days past the due date before it counts as overdue. Defaults to 0.'),
    ('job_plan', 'string', False, 'Job plan name, matched exactly.'),
    ('assigned_to', 'string', False, 'Username of an active user.'),
    ('description', 'string', False, 'Longer description.'),
    ('notes', 'string', False, 'Free-form notes.'),
]

# Examples are deliberately generic. These pages are public, so anything here
# is broadcast to anyone who can reach the application — they must never be
# copied from a real instance.
WORK_ORDER_EXAMPLE = {
    'wo_number': 'WO-2026-00007',
    'title': 'Air handler making a noise',
    'status': 'open',
    'priority': 'high',
    'type': 'unplanned',
    'asset_number': 'AST-00001',
    'asset_name': 'Air Handling Unit',
    'location_number': 'LOC-00011',
    'location_name': 'Plant Room',
    'job_plan': None,
    'assigned_to': None,
    'due_date': '2026-12-01',
    'completed_date': None,
    'overdue_grace_days': 0,
    'is_overdue': False,
    'description': 'Raised by an automation',
    'notes': None,
    'created_at': '2026-09-01T18:22:41',
    'url': 'http://localhost:5000/work-orders/7',
}

ERROR_EXAMPLE = {
    'error': 'The work order could not be created.',
    'errors': {'asset_number': "No asset exists with number 'AST-99999'."},
}

ENDPOINTS = [
    {
        'method': 'POST',
        'path': '/api/v1/work-orders',
        'endpoint': 'api.create_work_order_api',
        'summary': 'Create a work order',
        'description': (
            'Creates one work order and returns it. A number that does not resolve, '
            'or that resolves to a record which is not Active, is rejected — matching '
            'the rule the web interface enforces.'
        ),
        'body': WORK_ORDER_FIELDS,
        'example_request': {
            'title': 'Air handler making a noise',
            'asset_number': 'AST-00001',
            'priority': 'high',
            'due_date': '2026-12-01',
        },
        'responses': [
            (201, 'Created. The body is the new work order; `Location` points at it.',
             WORK_ORDER_EXAMPLE),
            (400, 'Invalid data. `errors` names each field that failed.', ERROR_EXAMPLE),
            (401, 'Missing or invalid token.', {'error': 'A valid API token is required.'}),
        ],
    },
    {
        'method': 'GET',
        'path': '/api/v1/work-orders',
        'endpoint': 'api.list_work_orders',
        'summary': 'List recent work orders',
        'description': 'Newest first.',
        'query': [
            ('status', 'string', f"Filter by status. One of: {', '.join(WO_STATUSES)}."),
            ('limit', 'integer', 'How many to return, 1–200. Defaults to 50.'),
        ],
        'responses': [
            (200, 'A count and the matching work orders.',
             {'count': 1, 'work_orders': [WORK_ORDER_EXAMPLE]}),
            (400, 'Unknown status filter.', {'error': 'Unknown status.'}),
        ],
    },
    {
        'method': 'GET',
        'path': '/api/v1/work-orders/{wo_number}',
        'endpoint': 'api.get_work_order',
        'summary': 'Fetch one work order',
        'description': 'Includes documents inherited from the PM, job plan, asset and location.',
        'path_params': [('wo_number', 'string', 'For example `WO-2026-00007`.')],
        'responses': [
            (200, 'The work order.',
             dict(WORK_ORDER_EXAMPLE, related_documents=[
                 {'filename': 'Air handler manual', 'source': 'Asset: Air Handling Unit'}])),
            (404, 'No such work order.', {'error': "No work order with number 'WO-2026-99999'."}),
        ],
    },
    {
        'method': 'GET',
        'path': '/api/v1/assets',
        'endpoint': 'api.list_assets',
        'summary': 'List assets',
        'description': 'Use this to discover the asset numbers you need to reference.',
        'responses': [
            (200, 'Every asset, with its parent and location.', {'count': 1, 'assets': [{
                'asset_number': 'AST-00004', 'name': 'Supply Fan', 'status': 'active',
                'location_number': 'LOC-00006', 'parent_asset_number': 'AST-00003',
                'parent_asset_name': 'Air Handling Unit',
                'path': 'Air Handling Unit › Supply Fan'}]}),
        ],
    },
    {
        'method': 'GET',
        'path': '/api/v1/locations',
        'endpoint': 'api.list_locations',
        'summary': 'List locations',
        'description': (
            'Use this to discover location numbers. `status` is one of: '
            + ', '.join(f'`{s}`' for s in LIFECYCLE_STATUSES) + '.'
        ),
        'responses': [
            (200, 'Every location, with its parent and full path.', {'count': 1, 'locations': [{
                'location_number': 'LOC-00002', 'name': 'Plant Room', 'status': 'active',
                'parent_location_number': 'LOC-00001', 'parent_location_name': 'Main Building',
                'path': 'Main Building › Plant Room'}]}),
        ],
    },
]


def _schema_from_fields(fields):
    return {
        'type': 'object',
        'required': [name for name, _t, required, _d in fields if required],
        'properties': {
            name: {'type': type_, 'description': description}
            for name, type_, _required, description in fields
        },
    }


def openapi_spec(base_url=None):
    """Build an OpenAPI 3.1 document from the definitions above."""
    paths = {}
    for spec in ENDPOINTS:
        operation = {
            'summary': spec['summary'],
            'description': spec.get('description', ''),
            'security': [{'bearerAuth': []}, {'apiKeyAuth': []}],
            'responses': {},
        }

        parameters = []
        for name, type_, description in spec.get('path_params', []):
            parameters.append({'name': name, 'in': 'path', 'required': True,
                               'schema': {'type': type_}, 'description': description})
        for name, type_, description in spec.get('query', []):
            parameters.append({'name': name, 'in': 'query', 'required': False,
                               'schema': {'type': type_}, 'description': description})
        if parameters:
            operation['parameters'] = parameters

        if spec.get('body'):
            operation['requestBody'] = {
                'required': True,
                'content': {'application/json': {
                    'schema': _schema_from_fields(spec['body']),
                    'example': spec.get('example_request'),
                }},
            }

        for status, description, example in spec['responses']:
            operation['responses'][str(status)] = {
                'description': description,
                'content': {'application/json': {'example': example}},
            }

        paths.setdefault(spec['path'], {})[spec['method'].lower()] = operation

    document = {
        'openapi': '3.1.0',
        'info': {'title': API_TITLE, 'version': API_VERSION, 'description': API_DESCRIPTION},
        'components': {
            'securitySchemes': {
                'bearerAuth': {'type': 'http', 'scheme': 'bearer',
                               'description': AUTH_DESCRIPTION},
                'apiKeyAuth': {'type': 'apiKey', 'in': 'header', 'name': 'X-API-Key',
                               'description': AUTH_DESCRIPTION},
            },
        },
        'security': [{'bearerAuth': []}, {'apiKeyAuth': []}],
        'paths': paths,
    }
    if base_url:
        document['servers'] = [{'url': base_url}]
    return document


def curl_example(spec, base_url):
    """A runnable curl line for the docs page."""
    import json

    path = spec['path']
    for name, _type, _description in spec.get('path_params', []):
        path = path.replace('{' + name + '}', 'WO-2026-00007')

    lines = [f"curl -X {spec['method']} {base_url}{path} \\",
             '  -H "Authorization: Bearer YOUR_TOKEN"']
    if spec.get('example_request'):
        lines[-1] += ' \\'
        lines.append('  -H "Content-Type: application/json" \\')
        body = json.dumps(spec['example_request'], indent=2)
        lines.append("  -d '" + body + "'")
    return '\n'.join(lines)
