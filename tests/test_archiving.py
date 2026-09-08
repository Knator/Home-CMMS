"""Archiving a work order: one-way, immutable, and frozen in time.

The point of archiving is that the record stops moving. Renaming an asset next
year must not rewrite what a job said on the day it was signed off — the same
reasoning that makes WorkOrderItem a copy of the job plan rather than a view of
it.
"""
import io

import pytest

from app.extensions import db as _db
from app.models.attachment import Attachment
from app.models.work_order import WorkOrder, WO_STATUSES
from app.services import (
    NotArchivable, archive_work_order, create_asset, create_location,
    create_work_order,
)
from tests.conftest import CSRF, prime_csrf


@pytest.fixture
def signed_in(client, db, user, login):
    login()
    prime_csrf(client)
    return client


@pytest.fixture
def completed(db):
    location = create_location(name='Utility Room')
    asset = create_asset(name='Furnace', location_id=location.id)
    wo = create_work_order(title='Annual service', wo_type='planned',
                           asset_id=asset.id, location_id=location.id,
                           status='completed')
    return wo


# ── only completed work may be archived ────────────────────────────────────

@pytest.mark.parametrize('status', ['open', 'in_progress', 'on_hold'])
def test_work_still_in_flight_cannot_be_archived(db, status):
    """An open or on-hold work order still has changes coming; freezing it
    would capture a record mid-job."""
    wo = create_work_order(title='Live one', wo_type='unplanned', status=status)
    with pytest.raises(NotArchivable, match='completed or cancelled'):
        archive_work_order(wo)
    assert wo.status == status


@pytest.mark.parametrize('status', ['completed', 'cancelled'])
def test_work_that_is_finished_with_can_be_archived(db, status):
    """Cancelled work is as finished as completed work — it is just finished
    without being done."""
    wo = create_work_order(title='Done with', wo_type='unplanned', status=status)
    archive_work_order(wo)
    assert wo.is_archived
    assert wo.status == status          # completed stays completed, cancelled cancelled
    assert wo.snapshot_value('asset_name') is None


def test_a_completed_work_order_can_be_archived(db, completed):
    archive_work_order(completed)
    assert completed.is_archived
    assert completed.archived_at is not None
    # The outcome survives: archiving is a flag, not a replacement status.
    assert completed.status == 'completed'


def test_archiving_twice_is_refused(db, completed):
    archive_work_order(completed)
    with pytest.raises(NotArchivable, match='already archived'):
        archive_work_order(completed)


# ── frozen in time ─────────────────────────────────────────────────────────

def test_renaming_an_asset_does_not_rewrite_an_archived_work_order(db, completed):
    """The whole point of the feature."""
    archive_work_order(completed)

    completed.asset.name = 'Old Furnace (replaced 2027)'
    completed.location.name = 'Basement Utility'
    _db.session.commit()

    assert completed.snapshot_value('asset_name') == 'Furnace'
    assert completed.snapshot_value('location_name') == 'Utility Room'
    # The live records did change; it is the archived view that does not follow.
    assert completed.asset.name == 'Old Furnace (replaced 2027)'


def test_the_snapshot_records_numbers_as_well_as_names(db, completed):
    archive_work_order(completed)
    assert completed.snapshot_value('asset_number') == completed.asset.asset_number
    assert completed.snapshot_value('location_number') == completed.location.location_number


def test_the_links_are_kept_so_the_asset_stays_reachable_and_protected(db, completed):
    """Freezing the display does not sever the record: the asset is still
    reachable, and still cannot be deleted with work logged against it."""
    from app.services import asset_delete_blockers

    asset = completed.asset
    archive_work_order(completed)

    assert completed.asset_id == asset.id
    assert asset_delete_blockers(asset)          # still blocked


def test_the_detail_page_shows_the_frozen_name(signed_in, db, completed):
    archive_work_order(completed)
    completed.asset.name = 'Renamed Furnace'
    _db.session.commit()

    html = signed_in.get(f'/work-orders/{completed.id}').get_data(as_text=True)
    assert 'Furnace' in html
    assert 'Renamed Furnace' not in html


# ── immutable ──────────────────────────────────────────────────────────────

def test_the_edit_page_is_refused(signed_in, db, completed):
    archive_work_order(completed)
    response = signed_in.get(f'/work-orders/{completed.id}/edit')
    assert response.status_code == 302


def test_editing_is_refused_even_when_posted_directly(signed_in, db, completed):
    """The hidden button is a convenience; the rule lives on the route."""
    archive_work_order(completed)
    signed_in.post(f'/work-orders/{completed.id}/edit', data={
        'csrf_token': CSRF, 'title': 'Rewritten', 'status': 'open',
    })
    assert _db.session.get(WorkOrder, completed.id).title == 'Annual service'


def test_it_cannot_be_taken_back_out_of_archived(signed_in, db, completed):
    archive_work_order(completed)
    signed_in.post(f'/work-orders/{completed.id}/edit', data={
        'csrf_token': CSRF, 'title': 'Annual service', 'status': 'open',
    })
    refreshed = _db.session.get(WorkOrder, completed.id)
    assert refreshed.is_archived            # the flag cannot be cleared
    assert refreshed.status == 'completed'  # nor the outcome rewritten


def test_archived_is_not_a_status_at_all(db):
    """It is a flag alongside the status, the way Maximo pairs a status with
    its history flag — so it cannot be reached from the status dropdown and
    cannot overwrite the outcome."""
    assert 'archived' not in WO_STATUSES


def test_an_archived_work_order_can_still_be_deleted(signed_in, db, completed):
    """Archiving freezes what the record says; it is not a retention lock."""
    wo_id = completed.id
    archive_work_order(completed)
    signed_in.post(f'/work-orders/{wo_id}/delete', data={'csrf_token': CSRF})
    assert _db.session.get(WorkOrder, wo_id) is None


def test_the_delete_panel_is_still_offered_when_archived(signed_in, db, completed):
    archive_work_order(completed)
    html = signed_in.get(f'/work-orders/{completed.id}').get_data(as_text=True)
    assert 'Delete Work Order' in html


def test_attachments_cannot_be_added(signed_in, db, completed):
    archive_work_order(completed)
    signed_in.post(f'/work-orders/{completed.id}/attachments', data={
        'csrf_token': CSRF, 'file': (io.BytesIO(b'x'), 'late.pdf'),
    }, content_type='multipart/form-data')
    assert Attachment.query.count() == 0


def test_existing_attachments_cannot_be_renamed_or_deleted(signed_in, db, completed):
    """The attachment routes are polymorphic, so the rule has to hold there too."""
    signed_in.post(f'/work-orders/{completed.id}/attachments', data={
        'csrf_token': CSRF, 'file': (io.BytesIO(b'x'), 'report.pdf'),
    }, content_type='multipart/form-data')
    att = Attachment.query.one()
    archive_work_order(completed)

    signed_in.post(f'/attachments/{att.id}/rename',
                   data={'csrf_token': CSRF, 'display_name': 'Changed'})
    signed_in.post(f'/attachments/{att.id}/delete', data={'csrf_token': CSRF})

    refreshed = _db.session.get(Attachment, att.id)
    assert refreshed is not None
    assert refreshed.display_name is None


# ── kept out of the way ────────────────────────────────────────────────────

def test_the_list_hides_archived_by_default(signed_in, db, completed):
    archive_work_order(completed)
    html = signed_in.get('/work-orders/').get_data(as_text=True)
    assert completed.wo_number not in html


def test_the_archive_filter_can_include_them(signed_in, db, completed):
    archive_work_order(completed)
    html = signed_in.get('/work-orders/?archived=show').get_data(as_text=True)
    assert completed.wo_number in html


def test_the_archive_filter_can_show_only_them(signed_in, db, completed):
    live = create_work_order(title='Still going', wo_type='unplanned')
    archive_work_order(completed)
    html = signed_in.get('/work-orders/?archived=only').get_data(as_text=True)
    assert completed.wo_number in html
    assert live.wo_number not in html


def test_the_archive_filter_is_independent_of_status(signed_in, db, completed):
    """The point of a separate box: filtering for completed work should not
    have to decide about archived work at the same time."""
    live = create_work_order(title='Also done', wo_type='unplanned',
                             status='completed')
    archive_work_order(completed)

    default = signed_in.get('/work-orders/?status=completed').get_data(as_text=True)
    assert live.wo_number in default
    assert completed.wo_number not in default

    both = signed_in.get(
        '/work-orders/?status=completed&archived=show').get_data(as_text=True)
    assert live.wo_number in both and completed.wo_number in both


def test_an_unknown_archive_filter_falls_back_to_hiding(signed_in, db, completed):
    archive_work_order(completed)
    html = signed_in.get('/work-orders/?archived=nonsense').get_data(as_text=True)
    assert completed.wo_number not in html


def test_the_dashboard_does_not_list_archived_work(signed_in, db, completed):
    archive_work_order(completed)
    assert completed.wo_number not in signed_in.get('/').get_data(as_text=True)


def test_the_location_page_does_not_list_archived_work(signed_in, db, completed):
    archive_work_order(completed)
    html = signed_in.get(f'/locations/{completed.location_id}').get_data(as_text=True)
    assert completed.wo_number not in html


# ── the API ────────────────────────────────────────────────────────────────

@pytest.fixture
def api(db, user):
    from app.models.api_token import ApiToken
    _, raw = ApiToken.issue(user, 'Test integration')
    _db.session.commit()
    return {'Authorization': f'Bearer {raw}'}


def test_the_api_hides_archived_by_default(client, db, api, completed):
    archive_work_order(completed)
    data = client.get('/api/v1/work-orders', headers=api).get_json()
    assert completed.wo_number not in [w['wo_number'] for w in data['work_orders']]


def test_show_archived_reveals_them(client, db, api, completed):
    archive_work_order(completed)
    data = client.get('/api/v1/work-orders?show_archived=true', headers=api).get_json()
    assert completed.wo_number in [w['wo_number'] for w in data['work_orders']]


def test_archived_is_not_a_valid_api_status_filter(client, db, api, completed):
    """It is a flag, so it is show_archived that reveals them, not a status."""
    response = client.get('/api/v1/work-orders?status=archived', headers=api)
    assert response.status_code == 400


def test_the_api_status_filter_is_independent_of_archiving(client, db, api,
                                                           completed):
    archive_work_order(completed)
    hidden = client.get('/api/v1/work-orders?status=completed',
                        headers=api).get_json()
    assert hidden['count'] == 0
    shown = client.get('/api/v1/work-orders?status=completed&show_archived=1',
                       headers=api).get_json()
    assert shown['count'] == 1


def test_fetching_an_archived_work_order_directly_is_hidden_too(client, db, api,
                                                                completed):
    archive_work_order(completed)
    assert client.get(f'/api/v1/work-orders/{completed.wo_number}',
                      headers=api).status_code == 404
    assert client.get(f'/api/v1/work-orders/{completed.wo_number}?show_archived=1',
                      headers=api).status_code == 200


def test_the_payload_says_whether_it_is_archived(client, db, api, completed):
    live = client.get(f'/api/v1/work-orders/{completed.wo_number}',
                      headers=api).get_json()
    assert live['archived'] is False
    archive_work_order(completed)
    frozen = client.get(f'/api/v1/work-orders/{completed.wo_number}?show_archived=1',
                        headers=api).get_json()
    assert frozen['archived'] is True


# ── archived attachments: readable, not editable ───────────────────────────

def attach_to(client, wo, filename='report.pdf'):
    client.post(f'/work-orders/{wo.id}/attachments', data={
        'csrf_token': CSRF, 'file': (io.BytesIO(b'x'), filename),
    }, content_type='multipart/form-data', follow_redirects=True)
    return Attachment.query.filter_by(original_filename=filename).one()


def test_archived_attachments_stay_viewable(signed_in, db, completed):
    att = attach_to(signed_in, completed)
    archive_work_order(completed)

    html = signed_in.get(f'/work-orders/{completed.id}').get_data(as_text=True)
    assert 'report.pdf' in html
    assert f'/attachments/{att.id}/inline' in html      # can still be opened
    assert f'/attachments/{att.id}/download' in html    # and downloaded
    assert signed_in.get(f'/attachments/{att.id}/download').status_code == 200


def test_the_page_stops_offering_what_it_will_not_accept(signed_in, db, completed):
    """The routes already refused these; the page was still showing the
    controls, which is what made it look as though they worked."""
    att = attach_to(signed_in, completed)
    archive_work_order(completed)
    html = signed_in.get(f'/work-orders/{completed.id}').get_data(as_text=True)

    assert f'/attachments/{att.id}/rename' not in html
    assert f'/attachments/{att.id}/delete' not in html
    assert f'/work-orders/{completed.id}/attachments' not in html   # no upload form


def test_a_live_work_order_keeps_all_its_attachment_controls(signed_in, db,
                                                             completed):
    att = attach_to(signed_in, completed)
    html = signed_in.get(f'/work-orders/{completed.id}').get_data(as_text=True)
    assert f'/attachments/{att.id}/rename' in html
    assert f'/attachments/{att.id}/delete' in html
    assert f'/work-orders/{completed.id}/attachments' in html


# ── the archived banner is a standing statement, not a flash ───────────────

def test_the_archived_banner_is_not_a_dismissible_flash(signed_in, db, completed):
    """It kept fading out after four seconds because it was styled as an
    .alert, which initAlerts() removes. It states a permanent property of the
    record, so it must not be one."""
    archive_work_order(completed)
    html = signed_in.get(f'/work-orders/{completed.id}').get_data(as_text=True)

    assert 'record-notice' in html
    banner_at = html.index('record-notice')
    banner = html[banner_at:banner_at + 400]
    assert 'Archived' in banner
    assert 'class="alert' not in banner


def test_a_live_work_order_has_no_such_banner(signed_in, db, completed):
    html = signed_in.get(f'/work-orders/{completed.id}').get_data(as_text=True)
    assert 'record-notice' not in html


def test_the_banner_survives_alongside_the_flash_that_follows_archiving(
        signed_in, db, completed):
    """Archiving redirects with a flash saying much the same thing. Both are on
    screen at once, so they must be distinguishable — one fades, one does not."""
    response = signed_in.post(f'/work-orders/{completed.id}/archive',
                              data={'csrf_token': CSRF}, follow_redirects=True)
    html = response.get_data(as_text=True)
    assert 'record-notice' in html          # the standing banner
    assert 'class="alert' in html           # and the transient flash
