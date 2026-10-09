"""Accounts: onboarding, login and employees."""
from fastapi import APIRouter, Depends
from fastapi.security import OAuth2PasswordRequestForm

from app.models import UserRole
from app.schemas import EmployeeCreate, OnboardingRequest
from app.security import get_current_user, require_role
from app.services import accounts

router = APIRouter()


@router.post("/onboard-business/")
def onboard_new_business(request: OnboardingRequest):
    return accounts.onboard_business(request)


@router.post("/login/")
def login(form_data: OAuth2PasswordRequestForm = Depends()):
    return accounts.login(form_data.username, form_data.password)


@router.post("/employees/")
def add_employee(
    employee_data: EmployeeCreate,
    current_user: dict = Depends(get_current_user) # THE BOUNCER
):
    # Optional Security: Only allow 'Owner' to add employees
    require_role(current_user, UserRole.OWNER, detail="Only business owners can add employees.")
    return accounts.add_employee(employee_data, current_user["business_id"])

