#!/usr/bin/env python3
"""
Script to create or promote a user to admin
Usage: python scripts/create_admin.py <user_email>
"""
import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import SessionLocal
from app.models import User

def create_admin(email: str):
    """Create or promote a user to admin"""
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == email).first()
        
        if not user:
            print(f"❌ User with email '{email}' not found.")
            print("\nAvailable users:")
            users = db.query(User).all()
            for u in users:
                print(f"  - {u.email} (ID: {u.id}, Name: {u.name})")
            return False
        
        user.is_admin = 1
        user.status = "active"
        db.commit()
        
        print(f"✅ User '{email}' (ID: {user.id}) is now an admin!")
        print(f"   Name: {user.name}")
        print(f"   Email: {user.email}")
        return True
        
    except Exception as e:
        print(f"❌ Error: {e}")
        db.rollback()
        return False
    finally:
        db.close()

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python scripts/create_admin.py <user_email>")
        print("\nExample: python scripts/create_admin.py user@example.com")
        sys.exit(1)
    
    email = sys.argv[1]
    create_admin(email)

