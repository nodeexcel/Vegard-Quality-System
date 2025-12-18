"""
Stripe payment service for credit purchases
"""
import stripe
import logging
from typing import Optional, Dict, Any
from app.config import settings

logger = logging.getLogger(__name__)

# Initialize Stripe
if settings.STRIPE_SECRET_KEY:
    stripe.api_key = settings.STRIPE_SECRET_KEY
else:
    logger.warning("Stripe secret key not configured. Payment features will be disabled.")

class StripeService:
    """Service for handling Stripe payment operations"""
    
    @staticmethod
    def create_customer(email: str, name: Optional[str] = None) -> Dict[str, Any]:
        """
        Create a Stripe customer
        Returns: Stripe customer object
        """
        if not settings.STRIPE_SECRET_KEY:
            raise ValueError("Stripe is not configured")
        
        customer_data = {
            "email": email,
        }
        if name:
            customer_data["name"] = name
        
        try:
            customer = stripe.Customer.create(**customer_data)
            return customer
        except stripe.error.StripeError as e:
            logger.error(f"Error creating Stripe customer: {str(e)}")
            raise
    
    @staticmethod
    def create_payment_intent(
        amount_nok: int,
        customer_id: Optional[str] = None,
        metadata: Optional[Dict[str, str]] = None
    ) -> Dict[str, Any]:
        """
        Create a Stripe PaymentIntent
        amount_nok: Amount in øre (e.g., 165000 for 1650 NOK)
        Returns: PaymentIntent object with client_secret
        """
        if not settings.STRIPE_SECRET_KEY:
            raise ValueError("Stripe is not configured")
        
        intent_data = {
            "amount": amount_nok,
            "currency": settings.STRIPE_CURRENCY,
            "automatic_payment_methods": {
                "enabled": True,
            },
        }
        
        if customer_id:
            intent_data["customer"] = customer_id
        
        if metadata:
            intent_data["metadata"] = metadata
        
        try:
            intent = stripe.PaymentIntent.create(**intent_data)
            return intent
        except stripe.error.StripeError as e:
            logger.error(f"Error creating payment intent: {str(e)}")
            raise
    
    @staticmethod
    def retrieve_payment_intent(payment_intent_id: str) -> Dict[str, Any]:
        """Retrieve a PaymentIntent by ID"""
        if not settings.STRIPE_SECRET_KEY:
            raise ValueError("Stripe is not configured")
        
        try:
            intent = stripe.PaymentIntent.retrieve(payment_intent_id)
            return intent
        except stripe.error.StripeError as e:
            logger.error(f"Error retrieving payment intent: {str(e)}")
            raise
    
    @staticmethod
    def create_checkout_session(
        amount_nok: int,
        success_url: str,
        cancel_url: str,
        customer_id: Optional[str] = None,
        metadata: Optional[Dict[str, str]] = None
    ) -> Dict[str, Any]:
        """
        Create a Stripe Checkout Session
        Returns: Checkout session with URL
        """
        if not settings.STRIPE_SECRET_KEY:
            raise ValueError("Stripe is not configured")
        
        session_data = {
            "payment_method_types": ["card"],
            "line_items": [{
                "price_data": {
                    "currency": settings.STRIPE_CURRENCY,
                    "product_data": {
                        "name": "Verifisert Credits",
                    },
                    "unit_amount": amount_nok,
                },
                "quantity": 1,
            }],
            "mode": "payment",
            "success_url": success_url,
            "cancel_url": cancel_url,
        }
        
        if customer_id:
            session_data["customer"] = customer_id
        
        if metadata:
            session_data["metadata"] = metadata
        
        try:
            session = stripe.checkout.Session.create(**session_data)
            return session
        except stripe.error.StripeError as e:
            logger.error(f"Error creating checkout session: {str(e)}")
            raise
    
    @staticmethod
    def construct_webhook_event(payload: bytes, sig_header: str) -> Dict[str, Any]:
        """
        Verify and construct webhook event from Stripe
        """
        if not settings.STRIPE_WEBHOOK_SECRET:
            raise ValueError("Stripe webhook secret not configured")
        
        try:
            event = stripe.Webhook.construct_event(
                payload, sig_header, settings.STRIPE_WEBHOOK_SECRET
            )
            return event
        except ValueError as e:
            logger.error(f"Invalid payload: {str(e)}")
            raise
        except stripe.error.SignatureVerificationError as e:
            logger.error(f"Invalid signature: {str(e)}")
            raise
    
    @staticmethod
    def refund_payment(payment_intent_id: str, amount: Optional[int] = None) -> Dict[str, Any]:
        """
        Refund a payment
        amount: Optional amount in øre. If None, full refund.
        """
        if not settings.STRIPE_SECRET_KEY:
            raise ValueError("Stripe is not configured")
        
        try:
            # Retrieve the payment intent to get the charge ID
            intent = stripe.PaymentIntent.retrieve(payment_intent_id)
            
            if not intent.charges.data:
                raise ValueError("No charges found for this payment intent")
            
            charge_id = intent.charges.data[0].id
            
            refund_data = {
                "charge": charge_id,
            }
            if amount:
                refund_data["amount"] = amount
            
            refund = stripe.Refund.create(**refund_data)
            return refund
        except stripe.error.StripeError as e:
            logger.error(f"Error creating refund: {str(e)}")
            raise

