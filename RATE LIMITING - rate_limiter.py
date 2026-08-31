# ============================================
# 1. RATE LIMITING - rate_limiter.py
# ============================================

from fastapi import Request, HTTPException
from fastapi.security import HTTPBearer
import time
from collections import defaultdict
from typing import Dict, Tuple
import redis.asyncio as redis
import json

class RateLimiter:
    """Distributed rate limiting using Redis"""
    
    def __init__(self, redis_client=None):
        self.redis_client = redis_client
        self.local_limits = defaultdict(list)
        
    async def is_rate_limited(self, key: str, limit: int, period: int = 60) -> Tuple[bool, int]:
        """
        Check if rate limit is exceeded
        Returns: (is_limited, remaining_requests)
        """
        if self.redis_client:
            # Distributed rate limiting with Redis
            current = int(time.time())
            window_key = f"rate_limit:{key}:{current // period}"
            
            # Increment counter
            count = await self.redis_client.incr(window_key)
            if count == 1:
                await self.redis_client.expire(window_key, period)
            
            if count > limit:
                ttl = await self.redis_client.ttl(window_key)
                return True, 0
            return False, limit - count
        else:
            # Local rate limiting (fallback)
            now = time.time()
            # Clean old requests
            self.local_limits[key] = [
                t for t in self.local_limits[key] 
                if now - t < period
            ]
            
            if len(self.local_limits[key]) >= limit:
                return True, 0
                
            self.local_limits[key].append(now)
            return False, limit - len(self.local_limits[key])

rate_limiter = RateLimiter()

# Rate limiting middleware
@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    """Rate limiting middleware for all requests"""
    
    # Get client IP
    client_ip = request.client.host
    user_agent = request.headers.get("user-agent", "unknown")
    key = f"{client_ip}:{user_agent}"
    
    # Different limits for different endpoints
    if request.url.path.startswith("/auth/"):
        limit, period = 10, 60  # 10 requests per minute for auth
    elif request.url.path.startswith("/hackathons/"):
        limit, period = 100, 60  # 100 requests per minute
    elif request.url.path.startswith("/judging/"):
        limit, period = 50, 60  # 50 requests per minute
    else:
        limit, period = 200, 60  # 200 requests per minute
    
    is_limited, remaining = await rate_limiter.is_rate_limited(key, limit, period)
    
    if is_limited:
        raise HTTPException(
            status_code=429,
            detail="Too many requests. Please try again later."
        )
    
    response = await call_next(request)
    response.headers["X-RateLimit-Limit"] = str(limit)
    response.headers["X-RateLimit-Remaining"] = str(remaining)
    response.headers["X-RateLimit-Reset"] = str(int(time.time()) + period)
    
    return response

# ============================================
# 2. LOGGING CONFIGURATION - logging_config.py
# ============================================

import logging
import sys
from logging.handlers import RotatingFileHandler, SMTPHandler
import json
from datetime import datetime
from pythonjsonlogger import jsonlogger

class JSONFormatter(jsonlogger.JsonFormatter):
    """Custom JSON formatter for structured logging"""
    
    def add_fields(self, log_record, record, message_dict):
        super(JSONFormatter, self).add_fields(log_record, record, message_dict)
        log_record['timestamp'] = datetime.utcnow().isoformat()
        log_record['level'] = record.levelname
        log_record['module'] = record.module
        log_record['function'] = record.funcName
        log_record['line'] = record.lineno

def setup_logging():
    """Configure application logging"""
    
    # Create logger
    logger = logging.getLogger('hacksupport')
    logger.setLevel(logging.INFO)
    
    # Console handler with JSON format
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(JSONFormatter())
    logger.addHandler(console_handler)
    
    # File handler with rotation
    file_handler = RotatingFileHandler(
        'logs/hacksupport.log',
        maxBytes=10_000_000,  # 10MB
        backupCount=5
    )
    file_handler.setFormatter(JSONFormatter())
    logger.addHandler(file_handler)
    
    # Error file handler
    error_handler = RotatingFileHandler(
        'logs/errors.log',
        maxBytes=5_000_000,  # 5MB
        backupCount=3
    )
    error_handler.setLevel(logging.ERROR)
    error_handler.setFormatter(JSONFormatter())
    logger.addHandler(error_handler)
    
    # Email handler for critical errors (optional)
    # email_handler = SMTPHandler(
    #     mailhost=('smtp.gmail.com', 587),
    #     fromaddr='errors@hacksupport.pro',
    #     toaddrs=['admin@hacksupport.pro'],
    #     subject='HackSupport Critical Error'
    # )
    # email_handler.setLevel(logging.CRITICAL)
    # logger.addHandler(email_handler)
    
    return logger

# Global logger instance
logger = setup_logging()

# ============================================
# 3. CACHE MANAGEMENT - cache.py
# ============================================

from functools import wraps
import hashlib
import json
import asyncio
from typing import Any, Callable, Optional
import redis.asyncio as redis

class CacheManager:
    """Redis-based caching manager"""
    
    def __init__(self, redis_url: str = "redis://localhost:6379"):
        self.redis = None
        self.redis_url = redis_url
        self.default_ttl = 300  # 5 minutes
        
    async def connect(self):
        """Connect to Redis"""
        if not self.redis:
            self.redis = await redis.from_url(
                self.redis_url,
                decode_responses=True
            )
        return self.redis
    
    async def get(self, key: str) -> Optional[Any]:
        """Get value from cache"""
        await self.connect()
        value = await self.redis.get(key)
        if value:
            return json.loads(value)
        return None
    
    async def set(self, key: str, value: Any, ttl: int = None):
        """Set value in cache with TTL"""
        await self.connect()
        ttl = ttl or self.default_ttl
        await self.redis.setex(
            key,
            ttl,
            json.dumps(value)
        )
    
    async def delete(self, key: str):
        """Delete from cache"""
        await self.connect()
        await self.redis.delete(key)
    
    async def clear_pattern(self, pattern: str):
        """Clear all keys matching pattern"""
        await self.connect()
        keys = await self.redis.keys(pattern)
        if keys:
            await self.redis.delete(*keys)
    
    def cached(self, ttl: int = None, key_prefix: str = ""):
        """Decorator for caching function results"""
        def decorator(func: Callable):
            @wraps(func)
            async def wrapper(*args, **kwargs):
                # Generate cache key
                cache_key = key_prefix or func.__name__
                args_str = json.dumps([args, kwargs], sort_keys=True)
                key_hash = hashlib.md5(args_str.encode()).hexdigest()
                full_key = f"{cache_key}:{key_hash}"
                
                # Try to get from cache
                cached_value = await self.get(full_key)
                if cached_value is not None:
                    logger.info(f"Cache hit: {full_key}")
                    return cached_value
                
                # Execute function
                result = await func(*args, **kwargs)
                
                # Cache result
                await self.set(full_key, result, ttl)
                logger.info(f"Cache miss: {full_key}")
                
                return result
            return wrapper
        return decorator

cache_manager = CacheManager()

# ============================================
# 4. BACKGROUND JOB SCHEDULER - scheduler.py
# ============================================

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
import asyncio

class JobScheduler:
    """Schedule background jobs"""
    
    def __init__(self):
        self.scheduler = AsyncIOScheduler()
        self.jobs = {}
        
    def start(self):
        """Start the scheduler"""
        self.scheduler.start()
        logger.info("Job scheduler started")
        
    def stop(self):
        """Stop the scheduler"""
        self.scheduler.shutdown()
        logger.info("Job scheduler stopped")
    
    def add_job(self, job_id: str, func: Callable, trigger: str, **kwargs):
        """Add a scheduled job"""
        if trigger == "cron":
            trigger_obj = CronTrigger(**kwargs)
        elif trigger == "interval":
            trigger_obj = IntervalTrigger(**kwargs)
        else:
            raise ValueError(f"Unknown trigger: {trigger}")
        
        job = self.scheduler.add_job(
            func,
            trigger_obj,
            id=job_id,
            replace_existing=True
        )
        self.jobs[job_id] = job
        logger.info(f"Job added: {job_id}")
        return job
    
    def remove_job(self, job_id: str):
        """Remove a scheduled job"""
        if job_id in self.jobs:
            self.scheduler.remove_job(job_id)
            del self.jobs[job_id]
            logger.info(f"Job removed: {job_id}")

scheduler = JobScheduler()

# Scheduled jobs
async def cleanup_expired_sessions():
    """Clean up expired user sessions"""
    logger.info("Running session cleanup")
    # Implementation would clean expired sessions from database

async def generate_daily_analytics():
    """Generate daily analytics report"""
    logger.info("Generating daily analytics")
    # Implementation would generate and store analytics

async def sync_external_services():
    """Sync with external services (GitHub, Slack, etc.)"""
    logger.info("Syncing external services")
    # Implementation would sync with external APIs

# Schedule jobs on startup
def setup_scheduled_jobs():
    """Set up all scheduled jobs"""
    
    # Run every hour
    scheduler.add_job(
        "cleanup_sessions",
        cleanup_expired_sessions,
        "interval",
        hours=1
    )
    
    # Run daily at midnight
    scheduler.add_job(
        "daily_analytics",
        generate_daily_analytics,
        "cron",
        hour=0,
        minute=0
    )
    
    # Run every 15 minutes
    scheduler.add_job(
        "sync_services",
        sync_external_services,
        "interval",
        minutes=15
    )
    
    scheduler.start()

# ============================================
# 5. ERROR HANDLING - error_handlers.py
# ============================================

from fastapi import Request, status
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError, IntegrityError
import traceback

@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """Handle HTTP exceptions"""
    logger.error(
        f"HTTP error: {exc.status_code} - {exc.detail}",
        extra={
            "path": request.url.path,
            "method": request.method,
            "client": request.client.host
        }
    )
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": exc.detail,
            "status_code": exc.status_code,
            "path": request.url.path
        }
    )

@app.exception_handler(SQLAlchemyError)
async def sqlalchemy_exception_handler(request: Request, exc: SQLAlchemyError):
    """Handle database errors"""
    logger.error(
        f"Database error: {str(exc)}",
        extra={
            "path": request.url.path,
            "error_type": type(exc).__name__,
            "traceback": traceback.format_exc()
        }
    )
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "error": "Database error occurred",
            "detail": "Please try again later"
        }
    )

@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception):
    """Handle all other exceptions"""
    logger.critical(
        f"Unhandled exception: {str(exc)}",
        extra={
            "path": request.url.path,
            "error_type": type(exc).__name__,
            "traceback": traceback.format_exc()
        }
    )
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "error": "Internal server error",
            "detail": "Please try again later"
        }
    )

# ============================================
# 6. API DOCUMENTATION - documentation.py
# ============================================

from fastapi.openapi.utils import get_openapi
from fastapi.openapi.docs import get_swagger_ui_html, get_redoc_html

def custom_openapi():
    """Custom OpenAPI schema with security schemes"""
    if app.openapi_schema:
        return app.openapi_schema
    
    openapi_schema = get_openapi(
        title="HackSupport.pro API",
        version="1.0.0",
        description="""
        ## HackSupport.pro - AI-Powered Hackathon Management Platform
        
        ### Features
        - **JudgeGPT**: AI-powered project evaluation
        - **HostGPT**: Intelligent hackathon coordination
        - **Real-time updates**: WebSocket support
        - **Scalable**: Microservices architecture
        
        ### Authentication
        Use JWT token obtained from `/auth/token` endpoint.
        
        ### Rate Limiting
        - Auth endpoints: 10 requests/minute
        - Hackathon endpoints: 100 requests/minute
        - Judging endpoints: 50 requests/minute
        - Default: 200 requests/minute
        """,
        routes=app.routes,
        servers=[
            {"url": "https://api.hacksupport.pro", "description": "Production"},
            {"url": "http://localhost:8000", "description": "Development"}
        ]
    )
    
    # Add security scheme
    openapi_schema["components"]["securitySchemes"] = {
        "bearerAuth": {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "JWT"
        }
    }
    
    # Add security requirements to all endpoints
    for path in openapi_schema["paths"]:
        for method in openapi_schema["paths"][path]:
            if "security" not in openapi_schema["paths"][path][method]:
                if "/auth" not in path and "/health" not in path:
                    openapi_schema["paths"][path][method]["security"] = [
                        {"bearerAuth": []}
                    ]
    
    app.openapi_schema = openapi_schema
    return app.openapi_schema

app.openapi = custom_openapi

@app.get("/docs", include_in_schema=False)
async def custom_swagger_ui_html():
    """Custom Swagger UI"""
    return get_swagger_ui_html(
        openapi_url="/openapi.json",
        title="HackSupport.pro API Documentation",
        swagger_js_url="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui-bundle.js",
        swagger_css_url="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui.css",
    )

@app.get("/redoc", include_in_schema=False)
async def redoc_html():
    """ReDoc documentation"""
    return get_redoc_html(
        openapi_url="/openapi.json",
        title="HackSupport.pro API Documentation - ReDoc"
    )

# ============================================
# 7. PAYMENT PROCESSING - payments.py
# ============================================

import stripe
from typing import Dict, Any
import os

stripe.api_key = os.getenv("STRIPE_SECRET_KEY", "sk_test_...")

class PaymentProcessor:
    """Handle subscription payments and licensing"""
    
    def __init__(self):
        self.stripe = stripe
        self.plans = {
            "starter": {
                "price_id": "price_starter",
                "amount": 4900,  # $49.00
                "currency": "usd",
                "features": ["up_to_50_projects", "judgegpt_basic", "hostgpt_essentials"]
            },
            "professional": {
                "price_id": "price_professional", 
                "amount": 14900,  # $149.00
                "currency": "usd",
                "features": ["up_to_500_projects", "judgegpt_pro", "hostgpt_advanced"]
            },
            "enterprise": {
                "price_id": "price_enterprise",
                "amount": "custom",
                "currency": "usd",
                "features": ["unlimited_projects", "full_ai_suite", "dedicated_support"]
            }
        }
    
    async def create_subscription(self, customer_id: str, plan: str) -> Dict[str, Any]:
        """Create a new subscription"""
        try:
            plan_config = self.plans.get(plan)
            if not plan_config:
                raise ValueError(f"Invalid plan: {plan}")
            
            subscription = self.stripe.Subscription.create(
                customer=customer_id,
                items=[{"price": plan_config["price_id"]}],
                payment_behavior="default_incomplete",
                expand=["latest_invoice.payment_intent"]
            )
            
            return {
                "subscription_id": subscription.id,
                "client_secret": subscription.latest_invoice.payment_intent.client_secret,
                "status": subscription.status
            }
        except Exception as e:
            logger.error(f"Payment error: {str(e)}")
            raise
    
    async def cancel_subscription(self, subscription_id: str) -> Dict[str, Any]:
        """Cancel a subscription"""
        try:
            subscription = self.stripe.Subscription.delete(subscription_id)
            return {
                "subscription_id": subscription_id,
                "status": "cancelled"
            }
        except Exception as e:
            logger.error(f"Cancellation error: {str(e)}")
            raise
    
    async def get_invoice(self, invoice_id: str) -> Dict[str, Any]:
        """Get invoice details"""
        try:
            invoice = self.stripe.Invoice.retrieve(invoice_id)
            return {
                "invoice_id": invoice.id,
                "amount": invoice.amount_due,
                "currency": invoice.currency,
                "status": invoice.status,
                "pdf_url": invoice.invoice_pdf
            }
        except Exception as e:
            logger.error(f"Invoice error: {str(e)}")
            raise

payment_processor = PaymentProcessor()

@app.post("/payments/subscribe")
async def create_subscription(
    plan: str,
    current_user: User = Depends(get_current_active_user)
):
    """Create a new subscription"""
    # Get or create Stripe customer
    customer = await get_or_create_customer(current_user)
    
    result = await payment_processor.create_subscription(customer.id, plan)
    return result

# ============================================
# 8. EMAIL TEMPLATES - email_templates.py
# ============================================

class EmailTemplates:
    """Email template management"""
    
    def __init__(self):
        self.templates = {}
    
    def get_template(self, template_name: str, data: Dict) -> str:
        """Get rendered email template"""
        templates = {
            "welcome": """
            <h1>Welcome to HackSupport.pro!</h1>
            <p>Hi {name},</p>
            <p>Welcome to the AI-powered hackathon management platform.</p>
            <p>Get started with your first hackathon today!</p>
            <a href="{dashboard_url}">Go to Dashboard</a>
            """,
            
            "project_evaluated": """
            <h1>Project Evaluation Complete</h1>
            <p>Hi {name},</p>
            <p>Your project <strong>{project_name}</strong> has been evaluated.</p>
            <div style="background: #f0f0f0; padding: 20px; border-radius: 10px;">
                <h3>Score: {score}/100</h3>
                <p>Feedback: {feedback}</p>
            </div>
            <a href="{project_url}">View Full Results</a>
            """,
            
            "hackathon_reminder": """
            <h1>Hackathon Reminder</h1>
            <p>Hi {name},</p>
            <p>Reminder: <strong>{hackathon_name}</strong> starts in {days} days!</p>
            <p>Start Date: {start_date}</p>
            <p>End Date: {end_date}</p>
            <a href="{hackathon_url}">View Hackathon</a>
            """,
            
            "task_assigned": """
            <h1>New Task Assigned</h1>
            <p>Hi {name},</p>
            <p>You have been assigned a new task:</p>
            <div style="background: #f0f0f0; padding: 15px; border-radius: 10px;">
                <h3>{task_title}</h3>
                <p>{task_description}</p>
                <p>Priority: {priority}</p>
                <p>Due Date: {due_date}</p>
            </div>
            <a href="{task_url}">View Task</a>
            """,
            
            "weekly_report": """
            <h1>Weekly Hackathon Report</h1>
            <p>Hi {name},</p>
            <p>Here's your weekly summary:</p>
            <ul>
                <li>Total Projects: {total_projects}</li>
                <li>Evaluations Completed: {evaluations}</li>
                <li>Average Score: {avg_score}</li>
                <li>Top Performer: {top_performer}</li>
            </ul>
            <a href="{report_url}">View Full Report</a>
            """
        }
        
        template = templates.get(template_name, "Hello {name}")
        return template.format(**data)

email_templates = EmailTemplates()

# ============================================
# 9. FILE UPLOAD HANDLING - uploads.py
# ============================================

import aiofiles
import hashlib
import os
from typing import Optional
from fastapi import UploadFile, File
import shutil
from pathlib import Path

class FileUploadService:
    """Handle file uploads with validation"""
    
    def __init__(self):
        self.upload_dir = "uploads"
        self.max_file_size = 50 * 1024 * 1024  # 50MB
        self.allowed_extensions = [
            '.pdf', '.doc', '.docx', '.txt', '.md',
            '.jpg', '.jpeg', '.png', '.gif',
            '.zip', '.rar', '.7z',
            '.mp4', '.mov', '.avi',
            '.mp3', '.wav'
        ]
        self.allowed_mime_types = [
            'application/pdf',
            'application/msword',
            'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            'text/plain',
            'text/markdown',
            'image/jpeg',
            'image/png',
            'image/gif',
            'application/zip',
            'video/mp4',
            'audio/mpeg'
        ]
        
        # Create upload directory if it doesn't exist
        Path(self.upload_dir).mkdir(exist_ok=True)
    
    async def validate_file(self, file: UploadFile) -> bool:
        """Validate uploaded file"""
        # Check file size
        content = await file.read()
        file_size = len(content)
        await file.seek(0)  # Reset file pointer
        
        if file_size > self.max_file_size:
            raise ValueError(f"File too large. Max size: {self.max_file_size/1024/1024}MB")
        
        # Check file extension
        extension = os.path.splitext(file.filename)[1].lower()
        if extension not in self.allowed_extensions:
            raise ValueError(f"File type not allowed: {extension}")
        
        # Check MIME type
        if file.content_type not in self.allowed_mime_types:
            raise ValueError(f"MIME type not allowed: {file.content_type}")
        
        return True
    
    async def upload_file(self, file: UploadFile, user_id: int, folder: str = "general") -> Dict:
        """Upload and process file"""
        try:
            # Validate file
            await self.validate_file(file)
            
            # Generate unique filename
            timestamp = int(time.time())
            file_hash = hashlib.md5(file.filename.encode() + str(timestamp).encode()).hexdigest()[:8]
            filename = f"{timestamp}_{file_hash}_{file.filename}"
            
            # Create user folder
            user_folder = Path(self.upload_dir) / folder / str(user_id)
            user_folder.mkdir(parents=True, exist_ok=True)
            
            # Save file
            file_path = user_folder / filename
            async with aiofiles.open(file_path, 'wb') as out_file:
                content = await file.read()
                await out_file.write(content)
            
            return {
                "filename": filename,
                "original_filename": file.filename,
                "size": os.path.getsize(file_path),
                "path": str(file_path),
                "url": f"/uploads/{folder}/{user_id}/{filename}",
                "mime_type": file.content_type
            }
            
        except Exception as e:
            logger.error(f"File upload error: {str(e)}")
            raise
    
    async def delete_file(self, file_path: str):
        """Delete a file"""
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
                return {"status": "deleted", "path": file_path}
            return {"status": "not_found", "path": file_path}
        except Exception as e:
            logger.error(f"File deletion error: {str(e)}")
            raise

file_service = FileUploadService()

@app.post("/upload")
async def upload_file(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_active_user)
):
    """Upload a file"""
    result = await file_service.upload_file(
        file,
        current_user.id,
        "projects"
    )
    return result

# ============================================
# 10. API VERSIONING - versioning.py
# ============================================

from fastapi import APIRouter
from fastapi.routing import APIRoute

class VersionedAPIRouter(APIRouter):
    """API router with version support"""
    
    def __init__(self, prefix: str = "", version: str = "v1", **kwargs):
        super().__init__(prefix=f"/api/{version}{prefix}", **kwargs)
        self.version = version

# Create versioned routers
v1_router = VersionedAPIRouter(version="v1")
v2_router = VersionedAPIRouter(version="v2")

# V1 endpoints
@v1_router.get("/hackathons")
async def get_hackathons_v1(current_user: User = Depends(get_current_active_user)):
    """V1 endpoint for hackathons"""
    return {"version": "v1", "data": []}

# V2 endpoints (new version with enhanced features)
@v2_router.get("/hackathons")
async def get_hackathons_v2(
    current_user: User = Depends(get_current_active_user),
    include_archived: bool = False
):
    """V2 endpoint with additional features"""
    return {
        "version": "v2",
        "include_archived": include_archived,
        "data": []
    }

# Include versioned routers
app.include_router(v1_router)
app.include_router(v2_router)

# ============================================
# 11. INITIALIZATION SCRIPT - init_db.py
# ============================================

async def initialize_database():
    """Initialize database with default data"""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    
    engine = create_engine(DATABASE_URL)
    Session = sessionmaker(bind=engine)
    db = Session()
    
    try:
        # Create default admin user
        admin = db.query(User).filter(User.username == "admin").first()
        if not admin:
            admin = User(
                username="admin",
                email="admin@hacksupport.pro",
                hashed_password=get_password_hash("admin123"),
                full_name="System Administrator",
                role=UserRole.ADMIN,
                is_active=True
            )
            db.add(admin)
            logger.info("Created default admin user")
        
        # Create default service provider
        provider = db.query(User).filter(User.username == "provider").first()
        if not provider:
            provider = User(
                username="provider",
                email="provider@hacksupport.pro",
                hashed_password=get_password_hash("provider123"),
                full_name="Default Service Provider",
                role=UserRole.SERVICE_PROVIDER,
                is_active=True
            )
            db.add(provider)
            logger.info("Created default service provider")
        
        db.commit()
        logger.info("Database initialization complete")
        
    except Exception as e:
        db.rollback()
        logger.error(f"Database initialization error: {str(e)}")
        raise
    finally:
        db.close()

# Run initialization on startup
@app.on_event("startup")
async def startup_event():
    """Run startup tasks"""
    logger.info("Starting HackSupport.pro...")
    
    # Initialize database
    await initialize_database()
    
    # Connect to Redis
    await cache_manager.connect()
    
    # Setup scheduled jobs
    setup_scheduled_jobs()
    
    # Initialize WebSocket manager Redis
    await manager.initialize_redis()
    
    logger.info("HackSupport.pro started successfully")