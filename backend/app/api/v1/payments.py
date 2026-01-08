"""
Payment API endpoints for Stripe credit purchases
"""
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Header, Body
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from typing import Optional, List
from pydantic import BaseModel
import logging
from datetime import datetime

from app.database import get_db
from app.models import User, CreditPackage, StripePayment, StripeCustomer, CreditTransaction
from app.auth import get_current_user
from app.services.stripe_service import StripeService
from app.config import settings
from typing import Optional

class PaymentIntentRequest(BaseModel):
    package_id: int

logger = logging.getLogger(__name__)

router = APIRouter()

# Initialize credit packages (will be seeded in database)
DEFAULT_PACKAGES = [
    {"name": "Beginner", "credits": 10, "price_nok": 1000},  # 10 NOK in øre
    {"name": "Starter", "credits": 30, "price_nok": 165000},  # 1650 NOK in øre
    {"name": "Standard", "credits": 100, "price_nok": 500000},  # 5000 NOK in øre
    {"name": "Pro", "credits": 300, "price_nok": 1350000},  # 13500 NOK in øre
    {"name": "Enterprise", "credits": 600, "price_nok": 2580000},  # 25800 NOK in øre
    {"name": "Ultra", "credits": 1500, "price_nok": 5700000},  # 57000 NOK in øre
]

@router.get("/packages")
async def get_credit_packages(
    db: Session = Depends(get_db)
):
    """
    Get available credit packages
    """
    packages = db.query(CreditPackage).filter(
        CreditPackage.is_active == 1
    ).order_by(CreditPackage.display_order, CreditPackage.price_nok).all()
    
    # If no packages exist, create default ones
    if not packages:
        logger.info("No credit packages found, creating default packages")
        for idx, pkg_data in enumerate(DEFAULT_PACKAGES):
            package = CreditPackage(
                name=pkg_data["name"],
                credits_amount=pkg_data["credits"],
                price_nok=pkg_data["price_nok"],
                is_active=1,
                display_order=idx
            )
            db.add(package)
        db.commit()
        db.refresh(package)
        packages = db.query(CreditPackage).filter(
            CreditPackage.is_active == 1
        ).order_by(CreditPackage.display_order, CreditPackage.price_nok).all()
    
    result = []
    for pkg in packages:
        result.append({
            "id": pkg.id,
            "name": pkg.name,
            "credits": pkg.credits_amount,
            "price_nok": pkg.price_nok / 100,  # Convert from øre to NOK
            "price_ore": pkg.price_nok,
            "price_per_credit": round((pkg.price_nok / 100) / pkg.credits_amount, 2),
            "reports": pkg.credits_amount // 10,  # Approximate reports (10 credits per report)
        })
    
    return {"packages": result}

@router.post("/create-intent")
async def create_payment_intent(
    request: PaymentIntentRequest = Body(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Create a Stripe PaymentIntent for credit purchase
    Requires package_id
    """
    if not settings.STRIPE_SECRET_KEY:
        raise HTTPException(status_code=503, detail="Payment system is not configured")
    
    package_id = request.package_id
    
    logger.info(f"Payment intent request - package_id: {package_id}")
    
    # Validate package_id is provided
    if not package_id or package_id == 0:
        raise HTTPException(status_code=400, detail="package_id is required")
    
    # Get package
    package = db.query(CreditPackage).filter(
        CreditPackage.id == package_id,
        CreditPackage.is_active == 1
    ).first()
    
    if not package:
        raise HTTPException(status_code=404, detail="Credit package not found")
    
    amount_nok = package.price_nok  # Already in øre
    credits = package.credits_amount
    package_name = package.name
    
    # Get or create Stripe customer
    stripe_customer = db.query(StripeCustomer).filter(
        StripeCustomer.user_id == current_user.id
    ).first()
    
    customer_id = None
    if stripe_customer:
        customer_id = stripe_customer.stripe_customer_id
    else:
        # Create new Stripe customer
        try:
            customer = StripeService.create_customer(
                email=current_user.email,
                name=current_user.name
            )
            customer_id = customer.id
            
            # Store in database
            stripe_customer = StripeCustomer(
                user_id=current_user.id,
                stripe_customer_id=customer_id
            )
            db.add(stripe_customer)
            db.commit()
        except Exception as e:
            logger.error(f"Error creating Stripe customer: {str(e)}")
            error_msg = str(e)
            if "Invalid API Key" in error_msg or "api key" in error_msg.lower():
                raise HTTPException(
                    status_code=500, 
                    detail="Stripe API key is invalid. Please check your STRIPE_SECRET_KEY in .env file. It should start with 'sk_test_' or 'sk_live_', not 'mk_' or 'pk_'"
                )
            raise HTTPException(status_code=500, detail=f"Failed to create payment customer: {str(e)}")
    
    # Create payment intent
    try:
        metadata = {
            "user_id": str(current_user.id),
            "credits": str(credits),
            "package_name": package_name,
            "package_id": str(package_id),
        }
        
        intent = StripeService.create_payment_intent(
            amount_nok=amount_nok,
            customer_id=customer_id,
            metadata=metadata
        )
        
        # Create payment record (pending)
        payment = StripePayment(
            user_id=current_user.id,
            stripe_payment_intent_id=intent.id,
            stripe_customer_id=customer_id,
            amount_nok=amount_nok,
            credits_purchased=credits,
            credit_package_id=package_id,
            status="pending"
        )
        db.add(payment)
        db.commit()
        
        return {
            "client_secret": intent.client_secret,
            "payment_intent_id": intent.id,
            "amount_nok": amount_nok / 100,
            "credits": credits
        }
    except Exception as e:
        logger.error(f"Error creating payment intent: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to create payment: {str(e)}")

@router.post("/webhook")
async def stripe_webhook(
    request: Request,
    stripe_signature: str = Header(None, alias="stripe-signature"),
    db: Session = Depends(get_db)
):
    """
    Handle Stripe webhook events
    """
    if not settings.STRIPE_WEBHOOK_SECRET:
        raise HTTPException(status_code=503, detail="Webhook secret not configured")
    
    payload = await request.body()
    
    try:
        event = StripeService.construct_webhook_event(payload, stripe_signature)
    except ValueError as e:
        logger.error(f"Invalid webhook payload: {str(e)}")
        raise HTTPException(status_code=400, detail="Invalid payload")
    except Exception as e:
        logger.error(f"Webhook signature verification failed: {str(e)}")
        raise HTTPException(status_code=400, detail="Invalid signature")
    
    # Handle the event
    event_type = event["type"]
    payment_intent = event["data"]["object"]
    payment_intent_id = payment_intent["id"]
    
    logger.info(f"Received Stripe webhook: {event_type} for payment {payment_intent_id}")
    
    # Find payment record
    payment = db.query(StripePayment).filter(
        StripePayment.stripe_payment_intent_id == payment_intent_id
    ).first()
    
    if not payment:
        logger.warning(f"Payment record not found for payment intent: {payment_intent_id}")
        return JSONResponse(content={"status": "ignored"})
    
    if event_type == "payment_intent.succeeded":
        # Payment succeeded - add credits to user
        if payment.status != "succeeded":
            user = db.query(User).filter(User.id == payment.user_id).first()
            if user:
                # Add credits
                user.credits += payment.credits_purchased
                
                # Create credit transaction
                transaction = CreditTransaction(
                    user_id=user.id,
                    amount=payment.credits_purchased,
                    transaction_type="purchase",
                    description=f"Purchased {payment.credits_purchased} credits via Stripe (Payment: {payment_intent_id})"
                )
                db.add(transaction)
                
                # Update payment status
                payment.status = "succeeded"
                payment.completed_at = datetime.utcnow()
                
                db.commit()
                logger.info(f"Added {payment.credits_purchased} credits to user {user.id} from payment {payment_intent_id}")
            else:
                logger.error(f"User not found for payment: {payment_intent_id}")
        else:
            logger.info(f"Payment {payment_intent_id} already processed")
    
    elif event_type == "payment_intent.payment_failed":
        # Payment failed
        payment.status = "failed"
        db.commit()
        logger.warning(f"Payment {payment_intent_id} failed")
    
    elif event_type == "payment_intent.canceled":
        # Payment canceled
        payment.status = "canceled"
        db.commit()
        logger.info(f"Payment {payment_intent_id} was canceled")
    
    return JSONResponse(content={"status": "success"})

@router.get("/history")
async def get_payment_history(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Get user's payment history
    """
    from sqlalchemy.orm import joinedload
    
    payments = db.query(StripePayment).options(
        joinedload(StripePayment.credit_package)
    ).filter(
        StripePayment.user_id == current_user.id
    ).order_by(StripePayment.created_at.desc()).offset(skip).limit(limit).all()
    
    total = db.query(StripePayment).filter(
        StripePayment.user_id == current_user.id
    ).count()
    
    result = []
    for payment in payments:
        package_name = None
        if payment.credit_package_id:
            # Load package if not already loaded
            if payment.credit_package:
                package_name = payment.credit_package.name
            else:
                # Fallback: query directly
                package = db.query(CreditPackage).filter(CreditPackage.id == payment.credit_package_id).first()
                if package:
                    package_name = package.name
        
        result.append({
            "id": payment.id,
            "payment_intent_id": payment.stripe_payment_intent_id,
            "amount_nok": payment.amount_nok / 100,
            "credits": payment.credits_purchased,
            "package_name": package_name,
            "status": payment.status,
            "created_at": payment.created_at.isoformat() if payment.created_at else None,
            "completed_at": payment.completed_at.isoformat() if payment.completed_at else None,
        })
    
    return {
        "payments": result,
        "total": total,
        "skip": skip,
        "limit": limit
    }

@router.get("/publishable-key")
async def get_publishable_key():
    """
    Get Stripe publishable key for frontend
    """
    return {
        "publishable_key": settings.STRIPE_PUBLISHABLE_KEY or ""
    }

