import logging
from datetime import datetime
from typing import Optional
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy.orm import Session

from models import SessionLocal, TrackingLink, ScrapingLog, SeenItem, Ad, DATABASE_URL
from scraper import scrape_and_notify, post_to_discord, scrape_ads_with_details
import json

logger = logging.getLogger(__name__)

# Global scheduler instance
_scheduler: Optional[AsyncIOScheduler] = None


def get_scheduler() -> AsyncIOScheduler:
    global _scheduler
    if _scheduler is None:
        jobstores = {"default": SQLAlchemyJobStore(url=DATABASE_URL)}
        _scheduler = AsyncIOScheduler(jobstores=jobstores)
    return _scheduler


def run_scraping_job(tracking_link_id: int):
    """Job function executed by scheduler"""
    db = SessionLocal()
    start_time = datetime.utcnow()

    try:
        link = (
            db.query(TrackingLink).filter(TrackingLink.id == tracking_link_id).first()
        )
        if not link or not link.is_active:
            logger.info(
                f"Skipping job for link {tracking_link_id}: not found or inactive"
            )
            return

        # Get seen items
        seen_ids = set(
            item.ad_id
            for item in db.query(SeenItem)
            .filter(SeenItem.tracking_link_id == tracking_link_id)
            .all()
        )

        # Scrape with full details for new items
        all_items, new_items, error_msg = scrape_ads_with_details(
            url=str(link.url),
            num_items=int(link.num_items),
            existing_ids=seen_ids,
        )

        execution_time = int((datetime.utcnow() - start_time).total_seconds() * 1000)

        if error_msg:
            # Log error
            log = ScrapingLog(
                tracking_link_id=tracking_link_id,
                status="error",
                items_found=0,
                items_posted=0,
                error_message=error_msg,
                execution_time_ms=execution_time,
            )
            db.add(log)
            db.commit()
            logger.error(f"Scraping error for link {tracking_link_id}: {error_msg}")
            return

        if not new_items:
            # No new items
            log = ScrapingLog(
                tracking_link_id=tracking_link_id,
                status="no_new",
                items_found=len(all_items),
                items_posted=0,
                execution_time_ms=execution_time,
            )
            db.add(log)
            link.last_run_at = datetime.utcnow()
            db.commit()
            logger.info(f"No new items for link {tracking_link_id}")
            return

        # Post to Discord
        posted_count, errors = post_to_discord(new_items, str(link.webhook_url))

        # Save seen items and full ad details
        for item in new_items:
            # Save to SeenItem
            seen = SeenItem(
                tracking_link_id=tracking_link_id,
                ad_id=item["id"],
                title=item.get("title", "")[:500],
            )
            db.add(seen)

            # Save full ad details (skip if already exists)
            existing_ad = db.query(Ad).filter(Ad.ad_id == item["id"]).first()
            if not existing_ad:
                ad = Ad(
                    ad_id=item["id"],
                    tracking_link_id=tracking_link_id,
                    title=item.get("title", "")[:500],
                    price=item.get("price", "")[:100],
                    location=item.get("location", "")[:255],
                    url=item.get("url", ""),
                    main_image_url=item.get("thumb", ""),
                    image_urls=json.dumps(item.get("image_urls", [])),
                    description=item.get("description", ""),
                    seller_name=item.get("seller_name", "")[:255],
                )
                db.add(ad)

        # Log success
        error_msg = "; ".join(errors) if errors else None
        log = ScrapingLog(
            tracking_link_id=tracking_link_id,
            status="success" if not errors else "partial",
            items_found=len(all_items),
            items_posted=posted_count,
            error_message=error_msg,
            execution_time_ms=execution_time,
        )
        db.add(log)
        link.last_run_at = datetime.utcnow()
        db.commit()

        logger.info(
            f"Posted {posted_count}/{len(new_items)} items for link {tracking_link_id}"
        )

    except Exception as e:
        logger.exception(f"Unexpected error in scraping job {tracking_link_id}")
        try:
            execution_time = int(
                (datetime.utcnow() - start_time).total_seconds() * 1000
            )
            log = ScrapingLog(
                tracking_link_id=tracking_link_id,
                status="error",
                items_found=0,
                items_posted=0,
                error_message=str(e),
                execution_time_ms=execution_time,
            )
            db.add(log)
            db.commit()
        except:
            pass
    finally:
        db.close()


def schedule_link(db: Session, link: TrackingLink):
    """Schedule or reschedule a tracking link"""
    scheduler = get_scheduler()
    job_id = f"scrape_{link.id}"

    # Remove existing job if any
    try:
        scheduler.remove_job(job_id)
    except:
        pass

    if link.is_active:
        scheduler.add_job(
            run_scraping_job,
            trigger=IntervalTrigger(minutes=link.interval_minutes),
            id=job_id,
            replace_existing=True,
            args=[link.id],
            misfire_grace_time=300,  # 5 minutes grace period
        )
        logger.info(f"Scheduled job {job_id} every {link.interval_minutes} minutes")


def unschedule_link(link_id: int):
    """Remove a scheduled job"""
    scheduler = get_scheduler()
    job_id = f"scrape_{link_id}"
    try:
        scheduler.remove_job(job_id)
        logger.info(f"Removed job {job_id}")
    except:
        pass


def init_scheduler(db: Session):
    """Initialize scheduler with all active links"""
    scheduler = get_scheduler()

    # Schedule all active links
    active_links = db.query(TrackingLink).filter(TrackingLink.is_active == True).all()
    for link in active_links:
        schedule_link(db, link)

    scheduler.start()
    logger.info(f"Scheduler started with {len(active_links)} active jobs")
