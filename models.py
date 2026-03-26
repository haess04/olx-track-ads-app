from datetime import datetime
from sqlalchemy import (
    create_engine,
    Column,
    Integer,
    String,
    Boolean,
    DateTime,
    Text,
    ForeignKey,
)
from sqlalchemy.orm import declarative_base, sessionmaker, relationship, backref
from pathlib import Path

Base = declarative_base()


class TrackingLink(Base):
    __tablename__ = "tracking_links"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False)
    url = Column(Text, nullable=False)
    webhook_url = Column(Text, nullable=False)
    num_items = Column(Integer, default=4)
    interval_minutes = Column(Integer, default=5)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    last_run_at = Column(DateTime, nullable=True)

    logs = relationship(
        "ScrapingLog", back_populates="tracking_link", cascade="all, delete-orphan"
    )
    seen_items = relationship(
        "SeenItem", back_populates="tracking_link", cascade="all, delete-orphan"
    )


class SeenItem(Base):
    __tablename__ = "seen_items"

    id = Column(Integer, primary_key=True, index=True)
    tracking_link_id = Column(Integer, ForeignKey("tracking_links.id"), nullable=False)
    ad_id = Column(String(512), nullable=False, index=True)
    title = Column(String(512))
    created_at = Column(DateTime, default=datetime.utcnow)

    tracking_link = relationship("TrackingLink", back_populates="seen_items")

    __table_args__ = (
        # Composite unique constraint per link + ad
        {"sqlite_autoincrement": True},
    )


class ScrapingLog(Base):
    __tablename__ = "scraping_logs"

    id = Column(Integer, primary_key=True, index=True)
    tracking_link_id = Column(Integer, ForeignKey("tracking_links.id"), nullable=False)
    status = Column(String(50), nullable=False)  # success, error, no_new
    items_found = Column(Integer, default=0)
    items_posted = Column(Integer, default=0)
    error_message = Column(Text, nullable=True)
    execution_time_ms = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    tracking_link = relationship("TrackingLink", back_populates="logs")


class Ad(Base):
    __tablename__ = "ads"

    id = Column(Integer, primary_key=True, index=True)
    ad_id = Column(String(512), nullable=False, index=True, unique=True)
    tracking_link_id = Column(Integer, ForeignKey("tracking_links.id"), nullable=True)
    title = Column(String(512), nullable=False)
    price = Column(String(100))
    location = Column(String(255))
    url = Column(Text, nullable=False)
    main_image_url = Column(Text)
    image_urls = Column(Text)  # JSON array of all image URLs
    description = Column(Text)
    seller_name = Column(String(255))
    posted_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    tracking_link = relationship("TrackingLink", backref="ads")


# Database setup
DATABASE_PATH = Path(__file__).parent / "olx_tracker.db"
DATABASE_URL = f"sqlite:///{DATABASE_PATH}"

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def init_db():
    Base.metadata.create_all(bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
