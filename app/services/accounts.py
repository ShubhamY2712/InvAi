"""Businesses and users: onboarding, login and adding employees."""
from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from app import db, models
from app.errors import BadRequest, Conflict, Unauthorized, Unavailable
from app.models import BusinessProfile, User
from app.schemas import EmployeeCreate, OnboardingRequest
from app.security import create_access_token, get_password_hash, verify_password

BUSINESS_ID_RETRIES = 5  # extra attempts, only when the generated business ID is already taken


def onboard_business(request: OnboardingRequest) -> dict:
    """Creates a business and its owner. Retries with a new business ID only if the generated one is taken."""
    hashed_password = get_password_hash(request.password)  # hashed once, not on every retry

    for _ in range(1 + BUSINESS_ID_RETRIES):
        with db.new_session() as session:
            new_business = BusinessProfile(
                id=models.generate_business_id(),
                business_name=request.business_name,
                category=request.category
            )
            # Create the Owner Profile; like every user, its id comes from the database sequence
            new_user = User(
                username=request.owner_username,
                email=request.email,
                hashed_password=hashed_password,
                role="Owner",
                business_id=new_business.id
            )
            session.add(new_business)
            session.add(new_user)

            try:
                session.commit()
            except IntegrityError as exc:
                session.rollback()
                duplicate = db.duplicate_field(exc)
                if duplicate == "business_id":
                    continue  # pick a new ID and try again
                if duplicate == "username":
                    raise Conflict(f"Username '{request.owner_username}' is already taken.")
                if duplicate == "email":
                    raise Conflict(f"Email '{request.email}' is already registered.")
                raise

            session.refresh(new_business)
            session.refresh(new_user)
            return {
                "success": True,
                "business_id": new_business.id,
                "owner_user_id": new_user.id
            }

    raise Unavailable("Couldn't allocate a unique business ID. Please try again.")


def login(username: str, password: str) -> dict:
    """Checks the credentials and returns a bearer token."""
    with db.new_session() as session:
        # 1. Search the database for the username the user typed in
        statement = select(User).where(User.username == username)
        user = session.exec(statement).first()

        # 2. If the user doesn't exist, kick them out
        if not user:
            raise Unauthorized("Incorrect username or password")

        # 3. If the user exists, run their typed password against the database hash
        is_password_correct = verify_password(password, user.hashed_password)

        if not is_password_correct:
            raise Unauthorized("Incorrect username or password")

        # 4. If everything matches, generate the VIP Wristband (JWT)
        # We store the user.id as the "sub" (subject) which is standard practice
        token_payload = {
            "sub": str(user.id),
            "business_id": str(user.business_id),
            "role": user.role
        }

        access_token = create_access_token(data=token_payload)

        # 5. Return the token in the exact format standard Next.js frontends expect
        return {
            "access_token": access_token,
            "token_type": "bearer"
        }


def add_employee(employee_data: EmployeeCreate, business_id: str) -> dict:
    with db.new_session() as session:
        # 1. Check if the username is already taken
        existing_user = session.exec(select(User).where(User.username == employee_data.username)).first()
        if existing_user:
            raise BadRequest("Username already exists.")

        # 2. Hash the employee's password before saving
        hashed_pw = get_password_hash(employee_data.password)

        # 3. Create the new User record linked to the Owner's business_id
        new_employee = User(
            username=employee_data.username,
            hashed_password=hashed_pw,
            email=employee_data.email,
            role=employee_data.role,
            business_id=business_id # SECURE LINKAGE
        )

        session.add(new_employee)
        session.commit()
        session.refresh(new_employee)

        return {
            "success": True,
            "message": f"Employee {new_employee.username} added to {business_id}",
            "employee_id": new_employee.id
        }
