import re

import pytest
from sqlmodel import Session, select

import main
from main import BusinessProfile, User, UserRole
from conftest import BUSINESS_ID, asgi_request

BUSINESS_ID_PATTERN = re.compile(r"^[ABCDEFGHJKLMNPQRSTUVWXYZ23456789]{8}$")


def onboard(**overrides):
    body = {"business_name": "New Shop", "category": main.BusinessCategory.RETAIL.value,
            "owner_username": "newowner", "email": "new@example.com", "password": "pw-123456"}
    return asgi_request("POST", "/onboard-business/", {**body, **overrides})


def counts(engine):
    with Session(engine) as session:
        return len(session.exec(select(BusinessProfile)).all()), len(session.exec(select(User)).all())


@pytest.fixture
def generated_ids(monkeypatch):
    """Replaces the ID generator with a fixed sequence of IDs; returns the list of IDs actually handed out."""
    handed_out = []
    def install(*ids, repeat_last=False):
        queue = list(ids)
        def fake():
            value = queue.pop(0) if len(queue) > 1 or not repeat_last else queue[0]
            handed_out.append(value)
            return value
        monkeypatch.setattr(main, "generate_business_id", fake)
    install.handed_out = handed_out
    return install


def test_generated_ids_are_8_chars_from_the_alphabet():
    ids = [main.generate_business_id() for _ in range(500)]
    assert all(BUSINESS_ID_PATTERN.match(i) for i in ids)
    assert len(set(ids)) == 500


def test_onboarding_creates_business_and_owner(engine):
    status, body = onboard()
    assert status == 200
    assert BUSINESS_ID_PATTERN.match(body["business_id"])
    with Session(engine) as session:
        owner = session.get(User, body["owner_user_id"])
    assert owner.username == "newowner" and owner.role == UserRole.OWNER
    assert owner.business_id == body["business_id"]
    assert main.verify_password("pw-123456", owner.hashed_password)


def test_owner_ids_come_from_the_sequence(engine):
    # conftest's owner already has id 1; no more {business_id}001 ids
    first = onboard()[1]
    second = onboard(owner_username="second", email="second@example.com")[1]
    assert (first["owner_user_id"], second["owner_user_id"]) == (2, 3)
    assert first["business_id"] != second["business_id"]


def test_duplicate_business_id_is_retried(engine, generated_ids):
    generated_ids(BUSINESS_ID, BUSINESS_ID, "NEWSHP23")  # the first two are already taken
    status, body = onboard()
    assert status == 200
    assert body["business_id"] == "NEWSHP23"
    assert generated_ids.handed_out == [BUSINESS_ID, BUSINESS_ID, "NEWSHP23"]
    assert counts(engine) == (3, 2)


def test_gives_up_with_503_after_five_retries(engine, generated_ids):
    generated_ids(BUSINESS_ID, repeat_last=True)  # every ID is taken
    status, body = onboard()
    assert status == 503
    assert body["detail"] == "Couldn't allocate a unique business ID. Please try again."
    assert len(generated_ids.handed_out) == 6  # first attempt + 5 retries
    assert counts(engine) == (2, 1)  # nothing created


def test_duplicate_username_is_409(engine, generated_ids):
    generated_ids("NEWSHP23", "OTHERID2")
    status, body = onboard(owner_username="owner")  # taken by conftest's owner
    assert status == 409
    assert body["detail"] == "Username 'owner' is already taken."
    assert generated_ids.handed_out == ["NEWSHP23"]  # not retried
    assert counts(engine) == (2, 1)  # the business insert was rolled back too


def test_duplicate_email_is_409(engine, generated_ids):
    generated_ids("NEWSHP23", "OTHERID2")
    status, body = onboard(email="owner@example.com")
    assert status == 409
    assert body["detail"] == "Email 'owner@example.com' is already registered."
    assert generated_ids.handed_out == ["NEWSHP23"]
    assert counts(engine) == (2, 1)


def test_duplicate_field_ignores_other_integrity_errors():
    class FakeOrig(Exception):
        pass
    exc = main.IntegrityError("INSERT ...", {}, FakeOrig('insert or update on table "sales" violates foreign key constraint'))
    assert main.duplicate_field(exc) is None
