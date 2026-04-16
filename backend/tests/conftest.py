"""
Test configuration — sets required env vars before any module import.
"""
import os

# Set required env vars for Settings() validation
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost:5432/test")
os.environ.setdefault("ODENGI_MERCHANT_ID", "test-merchant")
os.environ.setdefault("ODENGI_PASSWORD", "test-password")
os.environ.setdefault("PAYMENT_PROVIDER", "NAMBA_ONE")  # Use Namba One mock by default in tests
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
