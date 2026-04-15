from datetime import datetime
from typing import Optional
import uuid

from sqlalchemy import (
    Column, Integer, String, Float, Boolean, DateTime, Text,
    ForeignKey, JSON, Enum as SAEnum, BigInteger, func
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from pgvector.sqlalchemy import Vector

from database import Base


class Photographer(Base):
    __tablename__ = "photographers"

    id = Column(Integer, primary_key=True, index=True)
    google_id = Column(String(128), unique=True, index=True, nullable=False)
    email = Column(String(256), unique=True, index=True, nullable=False)
    name = Column(String(256), nullable=False)
    alias = Column(String(64), unique=True, index=True, nullable=True)
    avatar_url = Column(Text, nullable=True)
    phone = Column(String(32), nullable=True)
    instagram = Column(String(128), nullable=True)
    facebook = Column(String(128), nullable=True)
    address = Column(Text, nullable=True)
    bio = Column(Text, nullable=True)
    city = Column(String(128), nullable=True)
    website = Column(String(256), nullable=True)

    # Mercado Pago OAuth
    mp_access_token = Column(Text, nullable=True)
    mp_refresh_token = Column(Text, nullable=True)
    mp_user_id = Column(String(64), nullable=True)

    # Google Photos OAuth (scope separado del login principal)
    gp_access_token  = Column(Text, nullable=True)
    gp_refresh_token = Column(Text, nullable=True)

    # WaSender
    wa_session_id = Column(String(128), nullable=True)
    wa_token = Column(Text, nullable=True)
    wa_phone = Column(String(32), nullable=True)

    commission_rate = Column(Float, default=0.10)

    # Plan de suscripción
    plan = Column(SAEnum("free", "pro_monthly", "pro_annual", name="photographer_plan"), nullable=True)
    plan_expires_at    = Column(DateTime, nullable=True)
    mp_preapproval_id  = Column(String(256), nullable=True)
    policy_accepted_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)

    @property
    def gp_connected(self) -> bool:
        return self.gp_access_token is not None

    galleries = relationship("Gallery", back_populates="photographer")
    photos = relationship("Photo", back_populates="photographer")
    face_embeddings = relationship("FaceEmbedding", back_populates="photographer")
    persons = relationship("Person", back_populates="photographer")
    orders = relationship("Order", back_populates="photographer")


class Gallery(Base):
    __tablename__ = "galleries"

    id = Column(Integer, primary_key=True, index=True)
    photographer_id = Column(Integer, ForeignKey("photographers.id"), nullable=False)
    name = Column(String(256), nullable=False)
    description = Column(Text, nullable=True)
    location = Column(String(256), nullable=True)
    event_date = Column(DateTime, nullable=True)
    cover_photo_url = Column(Text, nullable=True)
    price_per_photo = Column(Float, default=0.0)
    status = Column(SAEnum("draft", "active", "inactive", "private", name="gallery_status"), default="private")
    visits = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)

    photographer = relationship("Photographer", back_populates="galleries")
    photos = relationship("Photo", back_populates="gallery")
    discount_rules = relationship("DiscountRule", back_populates="gallery", cascade="all, delete-orphan")
    orders = relationship("Order", back_populates="gallery")
    search_sessions = relationship("SearchSession", back_populates="gallery")


class DiscountRule(Base):
    __tablename__ = "discount_rules"

    id = Column(Integer, primary_key=True, index=True)
    gallery_id = Column(Integer, ForeignKey("galleries.id"), nullable=False)
    min_photos = Column(Integer, nullable=False)
    discount_percent = Column(Float, nullable=False)

    gallery = relationship("Gallery", back_populates="discount_rules")


class Photo(Base):
    __tablename__ = "photos"

    id = Column(Integer, primary_key=True, index=True)
    gallery_id = Column(Integer, ForeignKey("galleries.id"), nullable=False)
    photographer_id = Column(Integer, ForeignKey("photographers.id"), nullable=False)

    s3_key = Column(Text, nullable=True)             # original (null para fotos de Google Photos)
    s3_thumbnail_key = Column(Text, nullable=True)   # thumbnail
    s3_watermark_key = Column(Text, nullable=True)   # watermarked preview
    tiny_thumb = Column(Text, nullable=True)         # base64 32x32 pixelada (~400 bytes)

    filename = Column(String(512), nullable=False)
    file_size = Column(BigInteger, default=0)
    width = Column(Integer, nullable=True)
    height = Column(Integer, nullable=True)

    faces_count = Column(Integer, default=0)
    bib_numbers = Column(JSON, default=list)  # ["123", "456"]

    # Cloud provider (cuando la foto original queda en un servicio externo)
    cloud_provider = Column(String(32), nullable=True)   # "gphotos" | null
    cloud_file_id  = Column(String(256), nullable=True)  # mediaItem.id permanente de Google Photos

    # Desktop app — la foto original vive en la PC del fotógrafo
    path_hash      = Column(String(64), nullable=True, index=True)  # SHA-256 del path local
    desktop_source = Column(Boolean, default=False, server_default='false')

    upload_date = Column(DateTime, default=datetime.utcnow)
    processed = Column(Boolean, default=False)
    ocr_processed = Column(Boolean, server_default='false', default=False)

    gallery = relationship("Gallery", back_populates="photos")
    photographer = relationship("Photographer", back_populates="photos")
    face_embeddings = relationship("FaceEmbedding", back_populates="photo", cascade="all, delete-orphan")
    order_items = relationship("OrderItem", back_populates="photo")


class FaceEmbedding(Base):
    __tablename__ = "face_embeddings"

    id = Column(Integer, primary_key=True, index=True)
    photo_id = Column(Integer, ForeignKey("photos.id"), nullable=False)
    photographer_id = Column(Integer, ForeignKey("photographers.id"), nullable=False)

    embedding = Column(Vector(512), nullable=False)
    bbox = Column(JSON, nullable=True)  # {x, y, w, h}
    person_id = Column(Integer, ForeignKey("persons.id"), nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)

    photo = relationship("Photo", back_populates="face_embeddings")
    photographer = relationship("Photographer", back_populates="face_embeddings")
    person = relationship("Person", back_populates="face_embeddings")


class Person(Base):
    __tablename__ = "persons"

    id = Column(Integer, primary_key=True, index=True)
    photographer_id = Column(Integer, ForeignKey("photographers.id"), nullable=False)
    name = Column(String(256), nullable=True)

    representative_embedding = Column(Vector(512), nullable=True)
    photo_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)

    photographer = relationship("Photographer", back_populates="persons")
    face_embeddings = relationship("FaceEmbedding", back_populates="person")


class Buyer(Base):
    __tablename__ = "buyers"

    id = Column(Integer, primary_key=True)
    google_id = Column(String(128), unique=True, index=True, nullable=False)
    email = Column(String(256), unique=True, index=True, nullable=False)
    name = Column(String(256), nullable=True)
    avatar_url = Column(Text, nullable=True)
    mp_user_id = Column(String(64), nullable=True)   # de MP OAuth
    mp_verified = Column(Boolean, default=False)      # True cuando vincula MP
    created_at = Column(DateTime, default=datetime.utcnow)

    orders = relationship("Order", back_populates="buyer", foreign_keys="[Order.buyer_id]")


class Order(Base):
    __tablename__ = "orders"

    id = Column(Integer, primary_key=True, index=True)
    buyer_phone = Column(String(32), nullable=True)
    buyer_email = Column(String(256), nullable=True)
    buyer_name = Column(String(256), nullable=True)
    buyer_ip = Column(String(64), nullable=True)
    buyer_user_agent = Column(String(512), nullable=True)
    gallery_id = Column(Integer, ForeignKey("galleries.id"), nullable=False)
    photographer_id = Column(Integer, ForeignKey("photographers.id"), nullable=False)
    buyer_id = Column(Integer, ForeignKey("buyers.id"), nullable=True)

    mp_preference_id = Column(String(256), nullable=True)
    mp_payment_id = Column(String(256), nullable=True)

    status = Column(
        SAEnum("pending", "paid", "paid_pending_id", "delivered", "failed", "refunded", name="order_status"),
        default="pending"
    )

    total_amount = Column(Float, default=0.0)
    platform_fee = Column(Float, default=0.0)
    photographer_amount = Column(Float, default=0.0)

    email_sent_at = Column(DateTime, nullable=True)   # cuando se envió el email de entrega

    # Promo codes
    promo_code_applied = Column(String(32), nullable=True, index=True)  # Código promocional usado
    original_amount = Column(Float, nullable=True)  # Monto original sin descuento
    promo_discount_applied = Column(Float, default=0.0)  # Descuento total aplicado

    created_at = Column(DateTime, default=datetime.utcnow)

    gallery = relationship("Gallery", back_populates="orders")
    photographer = relationship("Photographer", back_populates="orders")
    buyer = relationship("Buyer", back_populates="orders", foreign_keys=[buyer_id])
    items = relationship("OrderItem", back_populates="order", cascade="all, delete-orphan")


class OrderItem(Base):
    __tablename__ = "order_items"

    id = Column(Integer, primary_key=True, index=True)
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=False)
    photo_id = Column(Integer, ForeignKey("photos.id"), nullable=False)
    price = Column(Float, default=0.0)

    order = relationship("Order", back_populates="items")
    photo = relationship("Photo", back_populates="order_items")


class DeliveryToken(Base):
    """Token único por orden para que el comprador acceda a sus fotos originales."""
    __tablename__ = "delivery_tokens"

    id = Column(Integer, primary_key=True, index=True)
    token = Column(UUID(as_uuid=True), unique=True, index=True, default=uuid.uuid4, nullable=False)
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=False)
    buyer_email = Column(String(256), nullable=False)
    expires_at = Column(DateTime, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    order = relationship("Order")


class SearchSession(Base):
    __tablename__ = "search_sessions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    phone_number = Column(String(32), nullable=True)
    gallery_id = Column(Integer, ForeignKey("galleries.id"), nullable=True)

    results = Column(JSON, nullable=True)  # JSONB
    search_type = Column(
        SAEnum("face", "bib", "text", name="search_type"),
        nullable=False
    )
    paid = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    visitor_ip = Column(String(64), nullable=True)
    visitor_user_agent = Column(String(512), nullable=True)

    gallery = relationship("Gallery", back_populates="search_sessions")


class PromoCode(Base):
    __tablename__ = "promo_codes"

    id = Column(Integer, primary_key=True, index=True)
    photographer_id = Column(Integer, ForeignKey("photographers.id"), nullable=True, index=True)  # NULL = código global
    code = Column(String(32), unique=True, index=True, nullable=False)  # "VERANO30", "GRATIS"
    description = Column(Text, nullable=True)  # Opcional: "Oferta de verano"
    discount_type = Column(SAEnum("percentage", "fixed", "free", name="promo_discount_type"), nullable=False)  # "percentage" | "fixed" | "free"
    discount_value = Column(Float, nullable=False, default=0.0)  # 30 para %, 5000 para valor fijo, 0 para gratis
    min_purchase = Column(Float, nullable=True)  # Precio mínimo de compra para aplicar
    max_discount = Column(Float, nullable=True)  # Máximo de descuento absoluto
    max_uses = Column(Integer, nullable=True)  # Máximo de usos totales
    max_uses_per_user = Column(Integer, nullable=True)  # Máximo de usos por usuario individual
    valid_from = Column(DateTime, nullable=True)  # Válido desde
    valid_until = Column(DateTime, nullable=True)  # Válido hasta
    active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    photographer = relationship("Photographer", backref="promo_codes")
    usages = relationship("PromoUsage", back_populates="promo_code", cascade="all, delete-orphan")


class PromoUsage(Base):
    __tablename__ = "promo_usage"

    id = Column(Integer, primary_key=True, index=True)
    promo_code_id = Column(Integer, ForeignKey("promo_codes.id", ondelete="CASCADE"), nullable=False, index=True)
    buyer_email = Column(String(256), nullable=True)
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=True)
    discount_applied = Column(Float, nullable=False, default=0.0)  # Descuento aplicado
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    promo_code = relationship("PromoCode", back_populates="usages")
    order = relationship("Order")
