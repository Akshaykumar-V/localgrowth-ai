"""Insert one fictional customer and purchase for a local demo."""

from sqlalchemy import select

from . import config  # noqa: F401
from .db import Base, SessionLocal, engine
from .models import Customer, Permission, Purchase, PurchaseItem, utcnow
from .security import encrypt_phone, phone_lookup_hash, validate_secrets


def main() -> None:
    validate_secrets()
    Base.metadata.create_all(bind=engine)
    phone = "+919876543210"  # Fictional demo number.
    with SessionLocal() as db:
        if db.scalar(select(Customer).where(Customer.phone_lookup_hash == phone_lookup_hash(phone))):
            print("Fictional demo customer already exists.")
            return
        now = utcnow()
        customer = Customer(
            name="Maya Demo",
            phone_ciphertext=encrypt_phone(phone),
            phone_lookup_hash=phone_lookup_hash(phone),
            phone_last4=phone[-4:],
            created_at=now,
        )
        db.add(customer)
        db.flush()
        db.add_all([
            Permission(customer_id=customer.id, permission_type="thank_you", allowed=True, choice_recorded=True, recorded_at=now),
            Permission(customer_id=customer.id, permission_type="promotional", allowed=True, choice_recorded=True, recorded_at=now),
        ])
        purchase = Purchase(customer_id=customer.id, total_amount=None, currency="INR", purchased_at=now)
        db.add(purchase)
        db.flush()
        db.add(PurchaseItem(purchase_id=purchase.id, name="Indigo demo kurti", category="Kurtis", quantity=1))
        db.commit()
    print("Inserted one fictional Maya Demo customer with a Kurtis purchase.")


if __name__ == "__main__":
    main()
