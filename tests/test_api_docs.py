"""The API reference, and the guard that keeps it honest.

The whole point of generating both views from one definition is that they cannot
drift from each other — but they could still drift from the code. The coverage
test below is what prevents that: add a route without documenting it and the
suite fails.
"""
import json

import pytest

from app.api import docs as api_docs
from tests.conftest import make_user

# Routes that document the API rather than being part of it.
META_ENDPOINTS = {'api.documentation', 'api.openapi'}


def flask_rule_to_openapi(rule):
    """/api/v1/work-orders/<wo_number>  ->  /api/v1/work-orders/{wo_number}"""
    import re
    return re.sub(r'<(?:[^:<>]+:)?([^<>]+)>', r'{\1}', rule)


# ── the drift guard ────────────────────────────────────────────────────────

def test_every_api_route_is_documented(app):
    documented = {(e['method'].upper(), e['path']) for e in api_docs.ENDPOINTS}

    missing = []
    for rule in app.url_map.iter_rules():
        if not rule.rule.startswith('/api/') or rule.endpoint in META_ENDPOINTS:
            continue
        for method in rule.methods - {'HEAD', 'OPTIONS'}:
            pair = (method, flask_rule_to_openapi(rule.rule))
            if pair not in documented:
                missing.append(f'{method} {pair[1]}')

    assert not missing, (
        'These API routes are not in app/api/docs.py, so the reference is out of '
        f'date: {sorted(missing)}'
    )


def test_the_docs_do_not_describe_routes_that_do_not_exist(app):
    real = set()
    for rule in app.url_map.iter_rules():
        if not rule.rule.startswith('/api/'):
            continue
        for method in rule.methods - {'HEAD', 'OPTIONS'}:
            real.add((method, flask_rule_to_openapi(rule.rule)))

    phantom = [f"{e['method']} {e['path']}" for e in api_docs.ENDPOINTS
               if (e['method'].upper(), e['path']) not in real]
    assert not phantom, f'Documented but not implemented: {phantom}'


def test_documented_endpoint_names_resolve(app):
    """Each entry names a real view function."""
    for spec in api_docs.ENDPOINTS:
        assert spec['endpoint'] in app.view_functions, spec['endpoint']


# ── the OpenAPI document ───────────────────────────────────────────────────

def test_spec_is_well_formed(app):
    with app.app_context():
        spec = api_docs.openapi_spec(base_url='http://example.test')

    assert spec['openapi'].startswith('3.')
    assert spec['info']['title'] and spec['info']['version']
    assert spec['servers'][0]['url'] == 'http://example.test'
    assert 'bearerAuth' in spec['components']['securitySchemes']
    # Round-trips as JSON, which is what clients will consume.
    assert json.loads(json.dumps(spec))


def test_spec_covers_each_endpoint_and_its_responses(app):
    with app.app_context():
        spec = api_docs.openapi_spec()

    for entry in api_docs.ENDPOINTS:
        operation = spec['paths'][entry['path']][entry['method'].lower()]
        assert operation['summary']
        for status, _desc, _example in entry['responses']:
            assert str(status) in operation['responses']


def test_create_documents_its_request_body(app):
    with app.app_context():
        spec = api_docs.openapi_spec()
    body = spec['paths']['/api/v1/work-orders']['post']['requestBody']
    schema = body['content']['application/json']['schema']
    assert 'title' in schema['required']
    assert 'asset_number' in schema['properties']


def test_the_spec_endpoint_serves_json(client, db, user, login):
    login()
    response = client.get('/api/v1/openapi.json')
    assert response.status_code == 200
    assert response.is_json
    assert response.get_json()['info']['title'] == api_docs.API_TITLE


# ── the human page ─────────────────────────────────────────────────────────

def test_docs_page_renders_every_endpoint(client, db, user, login):
    login()
    body = client.get('/api/v1/docs').get_data(as_text=True)
    for entry in api_docs.ENDPOINTS:
        assert entry['path'] in body
        assert entry['summary'] in body


def test_docs_page_explains_authentication(client, db, user, login):
    login()
    body = client.get('/api/v1/docs').get_data(as_text=True)
    assert 'Authorization: Bearer' in body
    assert 'X-API-Key' in body


def test_docs_page_shows_a_runnable_example(client, db, user, login):
    login()
    body = client.get('/api/v1/docs').get_data(as_text=True)
    assert 'curl -X POST' in body
    assert 'AST-00001' in body
    assert 'data-copy-target' in body        # copyable, like the token field


def test_docs_page_needs_no_internet(client, db, user, login):
    """A CDN-hosted Swagger UI would be a blank page on an offline LAN box."""
    login()
    body = client.get('/api/v1/docs').get_data(as_text=True)
    assert 'https://cdn' not in body
    assert 'unpkg.com' not in body
    assert 'swagger-ui' not in body.lower()


def test_docs_are_public(client, db):
    """No session needed, so tooling that cannot hold one can fetch the spec."""
    for path in ('/api/v1/docs', '/api/v1/openapi.json'):
        assert client.get(path).status_code == 200, path


def test_public_docs_expose_no_data(client, db, user):
    """They describe the shape of the API; every example is a fixed illustration."""
    from app.models.location import Location
    from app.services import create_asset, create_location, create_work_order

    secret_location = create_location(name='Zzyzx Private Room')
    secret_asset = create_asset(name='Confidential Boiler', location_id=secret_location.id)
    create_work_order(title='Do not disclose this title', asset_id=secret_asset.id)

    for path in ('/api/v1/docs', '/api/v1/openapi.json'):
        body = client.get(path).get_data(as_text=True)
        assert 'Zzyzx Private Room' not in body, path
        assert 'Confidential Boiler' not in body, path
        assert 'Do not disclose this title' not in body, path


def test_the_endpoints_themselves_are_still_protected(client, db, user):
    """Publishing the reference must not loosen anything it documents."""
    assert client.get('/api/v1/assets').status_code == 401
    assert client.get('/api/v1/locations').status_code == 401
    assert client.get('/api/v1/work-orders').status_code == 401
    assert client.post('/api/v1/work-orders', json={'title': 'x'}).status_code == 401


def test_the_page_renders_for_a_visitor_with_no_session(client, db):
    """base.html shows a username and Logout, which must not appear anonymously."""
    body = client.get('/api/v1/docs').get_data(as_text=True)
    assert 'Sign in' in body
    assert 'Logout' not in body
    assert 'Change Password' not in body
    assert 'Dashboard' not in body      # app navigation stays behind the login


def test_any_signed_in_user_may_read_them(client, db, login):
    make_user('ordinary', role='user')
    login('ordinary')
    assert client.get('/api/v1/docs').status_code == 200
    assert client.get('/api/v1/openapi.json').status_code == 200


def test_the_sidebar_links_to_the_docs(client, db, user, login):
    login()
    assert '/api/v1/docs' in client.get('/').get_data(as_text=True)


def test_curl_example_fills_in_path_parameters(app):
    entry = next(e for e in api_docs.ENDPOINTS if '{wo_number}' in e['path'])
    example = api_docs.curl_example(entry, 'http://example.test')
    assert '{wo_number}' not in example
    assert 'WO-2026-' in example


def test_examples_are_generic_not_copied_from_the_instance(app, db, user):
    """The reference is public, so an example lifted from a real deployment
    would broadcast someone's room and equipment names."""
    from app.services import create_asset, create_location, create_work_order

    location = create_location(name='Butler Pantry')
    asset = create_asset(name='Wine Fridge', location_id=location.id)
    create_work_order(title='Replace the compressor', asset_id=asset.id)

    with app.app_context():
        rendered = json.dumps(api_docs.openapi_spec())

    for private in ('Butler Pantry', 'Wine Fridge', 'Replace the compressor'):
        assert private not in rendered
