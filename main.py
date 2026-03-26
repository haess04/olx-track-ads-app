import os
import secrets
from contextlib import asynccontextmanager

# Load environment variables from .env file (if exists)
from dotenv import load_dotenv

load_dotenv()
from datetime import datetime, timedelta
from typing import Optional

import pytz
from fastapi import FastAPI, Request, Form, Depends, HTTPException, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session
from starlette.middleware.base import BaseHTTPMiddleware

from models import init_db, get_db, TrackingLink, ScrapingLog, Ad
import json
from scheduler import (
    get_scheduler,
    init_scheduler,
    schedule_link,
    unschedule_link,
    run_scraping_job,
)

# Authentication setup
security = HTTPBasic()

# Get auth credentials from environment variables
AUTH_USERNAME = os.getenv("AUTH_USERNAME")
AUTH_PASSWORD = os.getenv("AUTH_PASSWORD")
AUTH_ENABLED = AUTH_USERNAME and AUTH_PASSWORD


def verify_credentials(credentials: HTTPBasicCredentials):
    """Verify username and password using constant-time comparison."""
    # Use empty strings as fallback (auth will fail if not configured)
    expected_username = AUTH_USERNAME or ""
    expected_password = AUTH_PASSWORD or ""
    is_username_correct = secrets.compare_digest(
        credentials.username, expected_username
    )
    is_password_correct = secrets.compare_digest(
        credentials.password, expected_password
    )
    return is_username_correct and is_password_correct


class BasicAuthMiddleware(BaseHTTPMiddleware):
    """Middleware to enforce Basic Auth on all routes except excluded ones."""

    async def dispatch(self, request: Request, call_next):
        # Skip auth for health check (Render requirement) and static files
        path = request.url.path
        if path == "/health" or path.startswith("/static/"):
            return await call_next(request)

        # Skip auth if not enabled
        if not AUTH_ENABLED:
            return await call_next(request)

        # Check Authorization header
        auth_header = request.headers.get("Authorization")
        if not auth_header or not auth_header.startswith("Basic "):
            return HTMLResponse(
                content="Authentication required",
                status_code=status.HTTP_401_UNAUTHORIZED,
                headers={"WWW-Authenticate": "Basic"},
            )

        # Decode credentials
        import base64

        try:
            encoded = auth_header[6:]  # Remove "Basic "
            decoded = base64.b64decode(encoded).decode("utf-8")
            username, password = decoded.split(":", 1)
            credentials = HTTPBasicCredentials(username=username, password=password)

            if not verify_credentials(credentials):
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
        except Exception:
            return HTMLResponse(
                content="Invalid credentials",
                status_code=status.HTTP_401_UNAUTHORIZED,
                headers={"WWW-Authenticate": "Basic"},
            )

        return await call_next(request)


# Initialize templates
templates = Jinja2Templates(directory="templates")


# Add timezone filter for Poland (CET/CEST)
def to_poland_time(value):
    if value is None:
        return None
    utc = pytz.utc
    poland = pytz.timezone("Europe/Warsaw")
    utc_dt = utc.localize(value) if value.tzinfo is None else value
    return utc_dt.astimezone(poland)


def get_now():
    return datetime.now()


templates.env.filters["poland_time"] = to_poland_time
templates.env.globals["now"] = get_now


def get_poland_midnight():
    """Get today's midnight in Poland timezone"""
    poland = pytz.timezone("Europe/Warsaw")
    now = datetime.now(poland)
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight.astimezone(pytz.utc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager"""
    # Startup
    init_db()
    db = next(get_db())
    try:
        init_scheduler(db)
    finally:
        db.close()

    yield

    # Shutdown
    scheduler = get_scheduler()
    scheduler.shutdown()


app = FastAPI(title="OLX Tracker", lifespan=lifespan)

# Add Basic Auth middleware
app.add_middleware(BasicAuthMiddleware)

# Mount static files
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/health")
async def health_check():
    """Health check endpoint for keep-alive (Render)"""
    return {"status": "ok", "timestamp": datetime.utcnow().isoformat()}


@app.get("/", response_class=HTMLResponse)
async def index(request: Request, db: Session = Depends(get_db)):
    """Main page with tracking links list"""
    links = db.query(TrackingLink).order_by(TrackingLink.created_at.desc()).all()
    return templates.TemplateResponse(
        "index.html", {"request": request, "links": links}
    )


@app.post("/links")
async def create_link(
    request: Request,
    name: str = Form(...),
    url: str = Form(...),
    webhook_url: str = Form(...),
    num_items: int = Form(4),
    interval_minutes: int = Form(5),
    db: Session = Depends(get_db),
):
    """Create new tracking link"""
    link = TrackingLink(
        name=name,
        url=url,
        webhook_url=webhook_url,
        num_items=num_items,
        interval_minutes=interval_minutes,
        is_active=True,
    )
    db.add(link)
    db.commit()
    db.refresh(link)

    # Schedule the job
    schedule_link(db, link)

    return RedirectResponse(url="/", status_code=303)


@app.post("/links/{link_id}/toggle")
async def toggle_link(link_id: int, db: Session = Depends(get_db)):
    """Toggle link active/inactive"""
    link = db.query(TrackingLink).filter(TrackingLink.id == link_id).first()
    if not link:
        raise HTTPException(status_code=404, detail="Link not found")

    link.is_active = not link.is_active
    db.commit()

    # Update scheduler
    if link.is_active:
        schedule_link(db, link)
    else:
        unschedule_link(link_id)

    return RedirectResponse(url="/", status_code=303)


@app.post("/links/{link_id}/run")
async def run_link_now(link_id: int, db: Session = Depends(get_db)):
    """Manually trigger scraping for a link"""
    link = db.query(TrackingLink).filter(TrackingLink.id == link_id).first()
    if not link:
        raise HTTPException(status_code=404, detail="Link not found")

    # Run synchronously in background thread
    from concurrent.futures import ThreadPoolExecutor

    executor = ThreadPoolExecutor(max_workers=1)
    executor.submit(run_scraping_job, link_id)
    executor.shutdown(wait=False)

    return RedirectResponse(url="/", status_code=303)


@app.post("/links/{link_id}/delete")
async def delete_link(link_id: int, db: Session = Depends(get_db)):
    """Delete tracking link"""
    link = db.query(TrackingLink).filter(TrackingLink.id == link_id).first()
    if not link:
        raise HTTPException(status_code=404, detail="Link not found")

    # Unschedule first
    unschedule_link(link_id)

    # Delete from DB
    db.delete(link)
    db.commit()

    return RedirectResponse(url="/", status_code=303)


@app.get("/logs", response_class=HTMLResponse)
async def logs(request: Request, db: Session = Depends(get_db)):
    """Logs page with recent activity"""
    # Get recent logs with link info
    logs = (
        db.query(ScrapingLog).order_by(ScrapingLog.created_at.desc()).limit(100).all()
    )

    # Get today's midnight in Poland timezone (converted to UTC for DB query)
    poland_midnight = get_poland_midnight()

    # Calculate today's stats (Poland time)
    today_total = (
        db.query(ScrapingLog).filter(ScrapingLog.created_at >= poland_midnight).count()
    )
    today_success = (
        db.query(ScrapingLog)
        .filter(
            ScrapingLog.created_at >= poland_midnight, ScrapingLog.status == "success"
        )
        .count()
    )
    today_no_new = (
        db.query(ScrapingLog)
        .filter(
            ScrapingLog.created_at >= poland_midnight, ScrapingLog.status == "no_new"
        )
        .count()
    )
    today_errors = (
        db.query(ScrapingLog)
        .filter(
            ScrapingLog.created_at >= poland_midnight,
            ScrapingLog.status.in_(["error", "partial"]),
        )
        .count()
    )

    # Calculate overall stats (all time)
    overall_total = db.query(ScrapingLog).count()
    overall_success = (
        db.query(ScrapingLog).filter(ScrapingLog.status == "success").count()
    )
    overall_no_new = (
        db.query(ScrapingLog).filter(ScrapingLog.status == "no_new").count()
    )
    overall_errors = (
        db.query(ScrapingLog)
        .filter(ScrapingLog.status.in_(["error", "partial"]))
        .count()
    )

    # Calculate per-link today's stats
    from sqlalchemy import func as sa_func

    link_stats = []
    links = db.query(TrackingLink).all()
    for link in links:
        link_today = db.query(ScrapingLog).filter(
            ScrapingLog.tracking_link_id == link.id,
            ScrapingLog.created_at >= poland_midnight,
        )
        lt_total = link_today.count()
        lt_success = link_today.filter(ScrapingLog.status == "success").count()
        lt_no_new = link_today.filter(ScrapingLog.status == "no_new").count()
        lt_errors = link_today.filter(
            ScrapingLog.status.in_(["error", "partial"])
        ).count()

        link_stats.append(
            {
                "id": link.id,
                "name": link.name,
                "total": lt_total,
                "success": lt_success,
                "no_new": lt_no_new,
                "errors": lt_errors,
            }
        )

    return templates.TemplateResponse(
        "logs.html",
        {
            "request": request,
            "logs": logs,
            "today_stats": {
                "total": today_total,
                "success": today_success,
                "no_new": today_no_new,
                "errors": today_errors,
            },
            "overall_stats": {
                "total": overall_total,
                "success": overall_success,
                "no_new": overall_no_new,
                "errors": overall_errors,
            },
            "link_stats": link_stats,
        },
    )


@app.get("/ads", response_class=HTMLResponse)
async def ads_page(request: Request, db: Session = Depends(get_db)):
    """Display last 50 ads with images and details"""
    # Get last 50 ads ordered by creation date
    ads = db.query(Ad).order_by(Ad.created_at.desc()).limit(50).all()

    # Convert to plain dictionaries for JSON serialization
    ads_with_parsed_images = []
    for ad in ads:
        try:
            image_urls = json.loads(ad.image_urls) if ad.image_urls else []
        except:
            image_urls = []
        # Use first image from gallery as main image (better quality)
        main_image = image_urls[0] if image_urls else ad.main_image_url
        ads_with_parsed_images.append(
            {
                "ad": {
                    "id": ad.id,
                    "ad_id": ad.ad_id,
                    "title": ad.title,
                    "price": ad.price,
                    "location": ad.location,
                    "url": ad.url,
                    "main_image_url": main_image,
                    "description": ad.description,
                    "seller_name": ad.seller_name,
                    "created_at": ad.created_at.isoformat() if ad.created_at else None,
                },
                "image_urls": image_urls,
            }
        )

    return templates.TemplateResponse(
        "ads.html",
        {
            "request": request,
            "ads": ads_with_parsed_images,
        },
    )


if __name__ == "__main__":
    import uvicorn
    import os
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
