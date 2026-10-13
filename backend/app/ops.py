"""Restricted operational commands. Run only after migrations, as a platform owner."""
import argparse

from sqlalchemy import select

from app.db.session import SessionLocal
from app.models import PlatformOperator, User


def main() -> None:
    parser = argparse.ArgumentParser(description="Manage platform-operator membership")
    parser.add_argument("action", choices=("grant", "revoke"))
    parser.add_argument("email", help="Existing user email; the command never creates credentials")
    args = parser.parse_args()
    with SessionLocal.begin() as db:
        user = db.scalar(select(User).where(User.email == args.email.strip().lower()))
        if user is None:
            parser.error("User not found; create and verify the user account first.")
        operator = db.get(PlatformOperator, user.id)
        if args.action == "grant":
            if operator is None:
                db.add(PlatformOperator(user_id=user.id, is_active=True))
            else:
                operator.is_active = True
        elif operator is not None:
            db.delete(operator)
    print(f"Platform operator {args.action} applied for {args.email.strip().lower()}.")


if __name__ == "__main__":
    main()
