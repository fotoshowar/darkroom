"""
Darkroom - Darktable Editing Service
Servicio separado para edición automática de fotos con Darktable.
"""
import os
import shutil
import subprocess
import asyncio
import tempfile
import io
import json
from pathlib import Path
from typing import Optional
from datetime import datetime, timedelta

from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, BackgroundTasks, Query, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, text, func
from PIL import Image, ImageDraw
from rembg import remove
from jose import jwt, JWTError

from config import settings
from database import get_db
from models import Photographer, Gallery, Photo

app = FastAPI(
    title="Darkroom - Darktable Editing Service",
    description="Servicio de edición automática con Darktable",
    version="1.0.0",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
)

# ── CORS para Safari y navegadores ────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Montar archivos estáticos
app.mount("/static", StaticFiles(directory="static"), name="static")

# ── Configuración ────────────────────────────────────────────────────────────────

# Directorios
UPLOAD_DIR = Path("/tmp/darkroom_uploads")
PRESETS_DIR = Path("/tmp/darkroom_presets")
OUTPUT_DIR = Path("/tmp/darkroom_output")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
PRESETS_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# Estado de trabajos (en memoria para MVP, luego mover a Redis)
jobs = {}

# ── JWT helpers ────────────────────────────────────────────────────────────────

def decode_token(token: str) -> Optional[int]:
    """Decodifica JWT y retorna photographer_id."""
    try:
        payload = jwt.decode(token, settings.secret_key, algorithm=settings.algorithm)
        return int(payload["sub"])
    except (JWTError, KeyError, ValueError):
        return None


async def get_current_photographer_from_token(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> Photographer:
    """Obtiene el fotógrafo actual del token JWT (cookie o header)."""
    token = request.cookies.get("fotoshow_token") or (
        request.headers.get("Authorization", "").removeprefix("Bearer ").strip() or None
    )

    if not token:
        raise HTTPException(status_code=401, detail="No token provided")

    photographer_id = decode_token(token)
    if not photographer_id:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    result = await db.execute(
        select(Photographer).where(Photographer.id == photographer_id)
    )
    ph = result.scalar_one_or_none()
    if not ph:
        raise HTTPException(status_code=401, detail="Photographer not found")

    return ph


async def get_optional_photographer(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> Optional[Photographer]:
    """Obtiene el fotógrafo si hay token, sino retorna None (no falla)."""
    token = request.cookies.get("fotoshow_token") or (
        request.headers.get("Authorization", "").removeprefix("Bearer ").strip() or None
    )

    if not token:
        return None

    photographer_id = decode_token(token)
    if not photographer_id:
        return None

    result = await db.execute(
        select(Photographer).where(Photographer.id == photographer_id)
    )
    return result.scalar_one_or_none()

# ── Schemas ─────────────────────────────────────────────────────────────────────

class PresetUploadResponse(BaseModel):
    preset_id: str
    filename: str
    message: str

class CreateCustomPresetRequest(BaseModel):
    name: str
    brightness: float = 1.0
    contrast: float = 1.0
    saturation: float = 1.0
    sharpness: float = 1.0

class ProcessRequest(BaseModel):
    preset_id: str
    photo_ids: list[int]

class ProcessResponse(BaseModel):
    job_id: str
    status: str
    message: str

class JobResponse(BaseModel):
    job_id: str
    status: str
    progress: float
    total_photos: int
    processed_photos: int
    message: str
    output_urls: list[str]

class ExtractBackgroundResponse(BaseModel):
    success: bool
    message: str
    size: tuple[int, int] | None = None

class CompositeRequest(BaseModel):
    subject_filename: str
    template_filename: str
    scale_percent: int = 100
    offset_x: int = 0
    offset_y: int = 0
    # Sombra proyectada del sujeto sobre el fondo
    shadow_opacity: float = 0.0   # 0..1 — 0 desactiva la sombra
    shadow_blur: float = 15.0     # radio gaussiano (px)
    shadow_offset_x: int = 10
    shadow_offset_y: int = 20
    # Feather (suavizado) del borde del recorte
    feather_radius: float = 0.0   # radio gaussiano sobre el canal alfa
    # Vignette final
    vignette_strength: float = 0.0  # 0..1 — 0 desactiva
    vignette_size: float = 0.8      # 0.3 (apretado) .. 1.2 (ancho)

class AdjustmentsRequest(BaseModel):
    """Ajustes con Pillow para previsualizacion"""
    image_filename: str
    brightness: float = 1.0      # 0.5 = 50% más oscuro, 1.5 = 50% más claro
    contrast: float = 1.0        # 0.5 a 2.0
    saturation: float = 1.0      # 0.0 = B&N, 2.0 = muy saturado
    blur: float = 0.0            # 0.0 a 10.0
    sharpness: float = 1.0       # 0.0 a 2.0
    temperature: float = 0.0     # -100..100 (negativo frío/azul, positivo cálido/naranja)
    tint: float = 0.0            # -100..100 (negativo verde, positivo magenta)

class CompositeResponse(BaseModel):
    success: bool
    message: str
    filename: str | None = None

# ── Dependencias ─────────────────────────────────────────────────────────────────

async def get_current_photographer(db: AsyncSession = Depends(get_db)):
    """Obtener el fotógrafo actual del token."""
    # TODO: Implementar autenticación JWT (igual que FotoShow)
    # Por ahora, solo permitir si el email coincide
    # Esto se debe integrar con el sistema de autenticación de FotoShow
    raise HTTPException(status_code=501, detail="Autenticación no implementada aún")


# ── Endpoints ───────────────────────────────────────────────────────────────────

@app.get("/api/info")
async def info():
    """Información del servicio."""
    return {
        "service": "Darkroom - Darktable Editing Service",
        "status": "running",
        "version": "1.0.0"
    }


@app.get("/api/status")
async def status():
    """Estado del servicio."""
    # Verificar si darktable está instalado
    try:
        result = subprocess.run(["/usr/bin/darktable-cli", "--version"], capture_output=True, text=True)
        darktable_installed = True
        darktable_version = result.stdout.split("\n")[0] if result.stdout else "desconocido"
    except FileNotFoundError:
        darktable_installed = False
        darktable_version = "No instalado"

    return {
        "service": "Darkroom",
        "status": "running",
        "darktable_installed": darktable_installed,
        "darktable_version": darktable_version,
        "uploads_dir": str(UPLOAD_DIR),
        "presets_dir": str(PRESETS_DIR),
        "output_dir": str(OUTPUT_DIR),
    }


@app.post("/api/presets/upload", response_model=PresetUploadResponse)
async def upload_preset(
    file: UploadFile = File(...)
):
    """Subir un preset de Darktable (.xmp)."""
    if not file.filename or not file.filename.endswith('.xmp'):
        raise HTTPException(status_code=400, detail="Solo se permiten archivos .xmp")

    # Guardar preset
    preset_id = f"preset_{datetime.utcnow().timestamp()}"
    preset_path = PRESETS_DIR / f"{preset_id}.xmp"

    with open(preset_path, "wb") as f:
        content = await file.read()
        f.write(content)

    return PresetUploadResponse(
        preset_id=preset_id,
        filename=file.filename,
        message="Preset subido exitosamente"
    )


@app.post("/api/presets/create-custom", response_model=PresetUploadResponse)
async def create_custom_preset(request: CreateCustomPresetRequest):
    """Crear un preset personalizado con parámetros de filtros."""
    import json

    # Crear preset JSON con los parámetros
    preset_id = f"custom_{datetime.utcnow().timestamp()}"
    preset_filename = f"{preset_id}.json"
    preset_path = PRESETS_DIR / preset_filename

    # Guardar los parámetros como JSON
    preset_data = {
        "type": "custom",
        "name": request.name,
        "filters": {
            "brightness": request.brightness,
            "contrast": request.contrast,
            "saturation": request.saturation,
            "sharpness": request.sharpness
        }
    }

    with open(preset_path, "w") as f:
        json.dump(preset_data, f, indent=2)

    return PresetUploadResponse(
        preset_id=preset_id,
        filename=f"{request.name}.json",
        message="✅ Preset personalizado creado"
    )


@app.get("/api/presets/")
async def list_presets(photographer: Optional[Photographer] = Depends(get_optional_photographer)):
    """Listar todos los presets disponibles."""
    presets = []
    for preset_file in PRESETS_DIR.glob("*.xmp"):
        preset_id = preset_file.stem
        presets.append({
            "preset_id": preset_id,
            "filename": preset_file.name,
            "created_at": datetime.fromtimestamp(preset_file.stat().st_ctime)
        })

    return presets


@app.post("/api/process", response_model=ProcessResponse)
async def process_photos(
    request: ProcessRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db)
):
    """Procesar fotos con un preset de Darktable o personalizado."""
    # Verificar que el preset existe (puede ser .xmp o .json)
    preset_xmp = PRESETS_DIR / f"{request.preset_id}.xmp"
    preset_json = PRESETS_DIR / f"{request.preset_id}.json"

    if not preset_xmp.exists() and not preset_json.exists():
        raise HTTPException(status_code=404, detail="Preset no encontrado")

    # Obtener información de las fotos
    photos_data = []
    for photo_id in request.photo_ids:
        result = await db.execute(
            select(Photo).where(Photo.id == photo_id)
        )
        photo = result.scalar_one_or_none()
        if photo:
            photos_data.append({
                "id": photo.id,
                "s3_key": photo.s3_key,
                "s3_thumbnail_key": photo.s3_thumbnail_key
            })

    # Crear trabajo
    job_id = f"job_{datetime.utcnow().timestamp()}"
    jobs[job_id] = {
        "status": "pending",
        "progress": 0.0,
        "total_photos": len(photos_data),
        "processed_photos": 0,
        "message": "Procesando fotos...",
        "output_urls": [],
        "started_at": datetime.utcnow()
    }

    # Procesar en background
    background_tasks.add_task(
        process_photos_task,
        job_id,
        request.preset_id,
        photos_data
    )

    return ProcessResponse(
        job_id=job_id,
        status="pending",
        message="Procesamiento iniciado"
    )


def apply_photo_filters(image_path: Path, filters: dict) -> None:
    """Aplicar filtros a una imagen con Pillow."""
    from PIL import ImageEnhance

    try:
        img = Image.open(image_path)

        # Aplicar brillo
        if filters.get("brightness", 1.0) != 1.0:
            enhancer = ImageEnhance.Brightness(img)
            img = enhancer.enhance(filters["brightness"])

        # Aplicar contraste
        if filters.get("contrast", 1.0) != 1.0:
            enhancer = ImageEnhance.Contrast(img)
            img = enhancer.enhance(filters["contrast"])

        # Aplicar saturación
        if filters.get("saturation", 1.0) != 1.0:
            enhancer = ImageEnhance.Color(img)
            img = enhancer.enhance(filters["saturation"])

        # Aplicar nitidez
        if filters.get("sharpness", 1.0) != 1.0:
            enhancer = ImageEnhance.Sharpness(img)
            img = enhancer.enhance(filters["sharpness"])

        # Guardar imagen procesada
        img.save(image_path, quality=95)
        print(f"✓ Filtros aplicados: {filters}")
    except Exception as e:
        print(f"Error aplicando filtros: {e}")


async def process_photos_task(
    job_id: str,
    preset_id: str,
    photos_data: list[dict]
):
    """Tarea de background para procesar fotos."""
    import requests
    import json

    try:
        jobs[job_id]["status"] = "processing"

        # Directorio de salida para este trabajo
        job_output_dir = OUTPUT_DIR / job_id
        job_output_dir.mkdir(exist_ok=True)

        # Base URL de R2
        r2_base = "https://pub-4a2bb082723242e7abc54f95febc3df7.r2.dev"

        # Cargar parámetros del preset
        preset_filters = {}
        preset_path = PRESETS_DIR / f"{preset_id}.json"
        if preset_path.exists():
            try:
                with open(preset_path, "r") as f:
                    preset_data = json.load(f)
                    preset_filters = preset_data.get("filters", {})
                    print(f"✓ Preset personalizado: {preset_data.get('name', preset_id)}")
            except:
                pass

        # Procesar cada foto
        for i, photo_data in enumerate(photos_data):
            try:
                photo_id = photo_data["id"]
                s3_key = photo_data.get("s3_key")
                s3_thumbnail_key = photo_data.get("s3_thumbnail_key")

                output_file = job_output_dir / f"edited_{photo_id}.jpg"

                # Intentar descargar la original, si no está disponible usar miniatura
                photo_url = None
                if s3_key:
                    photo_url = f"{r2_base}/{s3_key}"
                elif s3_thumbnail_key:
                    photo_url = f"{r2_base}/{s3_thumbnail_key}"
                else:
                    raise ValueError(f"Foto {photo_id} no tiene s3_key ni s3_thumbnail_key")

                try:
                    # Descargar la foto desde R2 con requests
                    response = requests.get(photo_url, timeout=30)
                    response.raise_for_status()

                    with open(output_file, 'wb') as f:
                        f.write(response.content)

                    print(f"✓ Descargada foto {photo_id}")

                    # Aplicar filtros si existen
                    if preset_filters:
                        apply_photo_filters(output_file, preset_filters)

                except Exception as download_error:
                    print(f"✗ Error descargando foto {photo_id}: {download_error}")
                    raise

                # Agregar URL al resultado
                result_filename = output_file.name
                result_url = f"download/{job_id}/{result_filename}"
                jobs[job_id]["output_urls"].append(result_url)
                jobs[job_id]["processed_photos"] += 1
                jobs[job_id]["progress"] = (i + 1) / len(photos_data) * 100

            except Exception as e:
                print(f"Error procesando foto {photo_id}: {e}")
                jobs[job_id]["message"] = f"Error en foto {photo_id}: {str(e)}"

        jobs[job_id]["status"] = "completed"
        jobs[job_id]["message"] = f"✓ Procesamiento completado: {jobs[job_id]['processed_photos']}/{len(photos_data)} fotos"

    except Exception as e:
        jobs[job_id]["status"] = "failed"
        jobs[job_id]["message"] = f"Error: {str(e)}"
        print(f"Error en process_photos_task: {e}")


@app.get("/api/jobs/{job_id}", response_model=JobResponse)
async def get_job(job_id: str):
    """Obtener estado de un trabajo."""
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Trabajo no encontrado")

    job_data = jobs[job_id].copy()
    job_data["job_id"] = job_id
    return JobResponse(**job_data)


@app.get("/api/jobs/")
async def list_jobs():
    """Listar todos los trabajos."""
    return jobs


@app.get("/api/download/{job_id}/{filename}")
async def download_result(job_id: str, filename: str):
    """Descargar resultado procesado del trabajo."""
    job_output_dir = OUTPUT_DIR / job_id
    file_path = job_output_dir / filename

    # Validar que el archivo existe y está en el directorio correcto
    if not file_path.exists() or not str(file_path).startswith(str(OUTPUT_DIR)):
        raise HTTPException(status_code=404, detail="Archivo no encontrado")

    return FileResponse(
        path=file_path,
        media_type="image/jpeg",
        filename=filename
    )


# ── NUEVO ENDPOINT: Obtener galerías del fotógrafo ────────────────────────────

@app.get("/api/photographer-galleries", response_model=list[dict])
async def get_photographer_galleries(
    photographer_id: int = Query(None),
    db: AsyncSession = Depends(get_db)
):
    """Obtener todas las galerías de un fotógrafo."""
    if not photographer_id:
        raise HTTPException(status_code=400, detail="Requiere photographer_id")

    result = await db.execute(
        select(Gallery).where(Gallery.photographer_id == photographer_id)
    )
    galleries = result.scalars().all()

    gallery_list = []
    for g in galleries:
        # Contar fotos en cada galería
        count_result = await db.execute(
            select(func.count(Photo.id)).where(Photo.gallery_id == g.id)
        )
        photo_count = count_result.scalar() or 0

        gallery_list.append({
            "id": g.id,
            "name": g.name,
            "description": g.description,
            "cover_photo_url": g.cover_photo_url,
            "photo_count": photo_count
        })

    return gallery_list


@app.get("/api/gallery-photos")
async def get_gallery_photos(
    gallery_id: int = Query(None),
    page: int = Query(1, ge=1),
    db: AsyncSession = Depends(get_db)
):
    """Obtener fotos de una galería (paginadas, 10 por página)."""
    if not gallery_id:
        raise HTTPException(status_code=400, detail="Requiere gallery_id")

    # Contar total de fotos
    count_result = await db.execute(
        select(func.count(Photo.id)).where(Photo.gallery_id == gallery_id)
    )
    total_photos = count_result.scalar() or 0

    # Obtener fotos de esta página
    page_size = 10
    offset = (page - 1) * page_size

    result = await db.execute(
        select(Photo)
        .where(Photo.gallery_id == gallery_id)
        .offset(offset)
        .limit(page_size)
    )
    photos = result.scalars().all()

    # Base URL de R2 (CDN público)
    r2_base = "https://pub-4a2bb082723242e7abc54f95febc3df7.r2.dev"

    return {
        "total": total_photos,
        "page": page,
        "page_size": page_size,
        "photos": [
            {
                "id": photo.id,
                "filename": photo.filename,
                "width": photo.width,
                "height": photo.height,
                "tiny_thumb": photo.tiny_thumb,
                # Convertir ruta relativa a URL completa
                "thumbnail_url": f"{r2_base}/{photo.s3_thumbnail_key}" if photo.s3_thumbnail_key else None
            }
            for photo in photos
        ]
    }


@app.get("/api/photographer-photos", response_model=list[dict])
async def get_photographer_photos(
    photographer_id: int = Query(None),
    db: AsyncSession = Depends(get_db)
):
    """Obtener todas las fotos de un fotógrafo (requiere photographer_id como parámetro)."""
    if not photographer_id:
        raise HTTPException(status_code=400, detail="Requiere photographer_id como parámetro")

    try:
        # Obtener galerías del fotógrafo
        result_galleries = await db.execute(
            select(Gallery).where(Gallery.photographer_id == photographer_id)
        )
        galleries = result_galleries.scalars().all()

        response = []
        for gallery in galleries:
            # Obtener fotos de cada galería
            result_photos = await db.execute(
                select(Photo).where(Photo.gallery_id == gallery.id)
            )
            photos = result_photos.scalars().all()

            gallery_item = {
                "gallery_id": gallery.id,
                "gallery_name": gallery.name,
                "photos": [
                    {
                        "id": photo.id,
                        "filename": photo.filename,
                        "width": photo.width,
                        "height": photo.height,
                        "s3_key": photo.s3_key,
                        "s3_thumbnail_key": photo.s3_thumbnail_key,
                        "tiny_thumb": photo.tiny_thumb,  # base64 para preview rápido
                        "gallery_id": gallery.id
                    }
                    for photo in photos
                ]
            }
            response.append(gallery_item)

        return response
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error al obtener fotos: {str(e)}")


# ── NUEVOS ENDPOINTS: Extracción de fondo + Composición ──────────────────

@app.post("/api/upload-image")
async def upload_image(
    file: UploadFile = File(...),
    
):
    """Subir una imagen (sujeto o plantilla)."""
    if not file.filename:
        raise HTTPException(status_code=400, detail="Nombre de archivo requerido")

    # Validar extensión
    allowed_ext = {'.jpg', '.jpeg', '.png', '.webp'}
    file_ext = Path(file.filename).suffix.lower()
    if file_ext not in allowed_ext:
        raise HTTPException(status_code=400, detail="Solo se permiten JPG, PNG, WEBP")

    # Guardar archivo
    upload_id = f"img_{datetime.utcnow().timestamp()}"
    filename = f"{upload_id}{file_ext}"
    filepath = UPLOAD_DIR / filename

    content = await file.read()
    with open(filepath, "wb") as f:
        f.write(content)

    # Obtener dimensiones
    img = Image.open(filepath)
    return {
        "success": True,
        "filename": filename,
        "width": img.width,
        "height": img.height,
        "message": "Imagen subida exitosamente"
    }


@app.post("/api/extract-background")
async def extract_background(
    file: UploadFile = File(...),
    
):
    """Extraer el fondo de una imagen usando rembg."""
    if not file.filename:
        raise HTTPException(status_code=400, detail="Nombre de archivo requerido")

    try:
        # Leer imagen
        content = await file.read()
        input_img = Image.open(io.BytesIO(content)).convert("RGBA")

        # Extraer fondo (en background para no bloquear)
        output_img = await asyncio.to_thread(remove, input_img)

        # Guardar resultado
        output_id = f"nobg_{datetime.utcnow().timestamp()}"
        output_filename = f"{output_id}.png"
        output_path = UPLOAD_DIR / output_filename

        output_img.save(output_path, "PNG")

        return {
            "success": True,
            "filename": output_filename,
            "width": output_img.width,
            "height": output_img.height,
            "message": "Fondo extraído exitosamente"
        }
    except Exception as e:
        return {
            "success": False,
            "filename": None,
            "message": f"Error al extraer fondo: {str(e)}"
        }


def _apply_temp_tint(img: Image.Image, temperature: float, tint: float) -> Image.Image:
    """Aplicar temperatura (frío↔cálido) y tinte (verde↔magenta) sobre una imagen RGBA.

    temperature ∈ [-100, 100]  (negativo enfría con boost azul, positivo calienta con boost rojo)
    tint ∈ [-100, 100]         (negativo empuja a verde, positivo a magenta)
    """
    import numpy as np
    arr = np.array(img.convert("RGBA"), dtype=np.float32)
    r, g, b, a = arr[..., 0], arr[..., 1], arr[..., 2], arr[..., 3]
    if temperature != 0.0:
        t = temperature / 200.0  # ±0.5 en los extremos
        r = r * (1.0 + t)
        b = b * (1.0 - t)
    if tint != 0.0:
        s = tint / 200.0
        # Positivo (magenta): boost R+B, reduce G
        # Negativo (verde): inverso
        r = r * (1.0 + s / 2)
        b = b * (1.0 + s / 2)
        g = g * (1.0 - s)
    out = np.stack([np.clip(r, 0, 255), np.clip(g, 0, 255), np.clip(b, 0, 255), a], axis=-1).astype(np.uint8)
    return Image.fromarray(out, mode="RGBA")


def _apply_vignette(img: Image.Image, strength: float, size: float) -> Image.Image:
    """Vignette radial sobre una imagen RGB.

    strength ∈ [0, 1]  — 0 desactiva; 1 oscurece fuerte los bordes
    size     ∈ [0.3, 1.5]  — radio relativo donde arranca el oscurecimiento
    """
    if strength <= 0:
        return img
    import numpy as np
    w, h = img.size
    cx, cy = w / 2.0, h / 2.0
    yy, xx = np.mgrid[0:h, 0:w]
    dist = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2)
    max_d = np.sqrt(cx ** 2 + cy ** 2)
    # Factor: 1 en el centro, 0 en los bordes; ajustar por size
    norm = np.clip(dist / (max_d * max(0.1, size)), 0, 1)
    mask = 1.0 - (norm ** 2) * strength  # curva suave cuadrática
    arr = np.array(img.convert("RGB"), dtype=np.float32)
    arr[..., 0] *= mask
    arr[..., 1] *= mask
    arr[..., 2] *= mask
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), mode="RGB")


@app.post("/api/composite", response_model=dict)
async def composite_images(
    request: CompositeRequest,

):
    """Componer imagen de sujeto (sin fondo) sobre plantilla usando filenames guardados.

    Args:
        subject_filename: Filename del sujeto sin fondo
        template_filename: Filename de la plantilla
        scale_percent: Escala en porcentaje (100 = tamaño original)
        offset_x: Offset X desde el centro en píxeles
        offset_y: Offset Y desde el centro en píxeles
    """
    try:
        # Validar que los archivos existan
        subject_path = UPLOAD_DIR / request.subject_filename
        template_path = UPLOAD_DIR / request.template_filename

        if not subject_path.exists():
            raise HTTPException(status_code=404, detail=f"Archivo de sujeto no encontrado: {request.subject_filename}")
        if not template_path.exists():
            raise HTTPException(status_code=404, detail=f"Archivo de plantilla no encontrado: {request.template_filename}")

        from PIL import ImageFilter

        # Leer imágenes desde archivos guardados
        subject_img = Image.open(subject_path).convert("RGBA")
        template_img = Image.open(template_path).convert("RGBA")

        # Calcular nuevo tamaño basado en porcentaje
        new_width = int(subject_img.width * request.scale_percent / 100)
        new_height = int(subject_img.height * request.scale_percent / 100)

        # Redimensionar sujeto
        if new_width > 0 and new_height > 0:
            subject_img = subject_img.resize((new_width, new_height), Image.LANCZOS)

        # Feather: suavizar el canal alfa del sujeto para eliminar el halo del recorte
        if request.feather_radius and request.feather_radius > 0:
            a = subject_img.split()[3]
            a = a.filter(ImageFilter.GaussianBlur(radius=request.feather_radius))
            subject_img.putalpha(a)

        # Calcular posición centrada + offsets
        center_x = (template_img.width - new_width) // 2 + request.offset_x
        center_y = (template_img.height - new_height) // 2 + request.offset_y

        # No clampear: queremos permitir que sobresalga (recortado por el canvas)

        # Sombra proyectada (si opacity > 0)
        if request.shadow_opacity and request.shadow_opacity > 0:
            shadow_alpha = subject_img.split()[3].copy()
            op = max(0.0, min(1.0, float(request.shadow_opacity)))
            shadow_alpha = shadow_alpha.point(lambda p: int(p * op))
            if request.shadow_blur and request.shadow_blur > 0:
                shadow_alpha = shadow_alpha.filter(ImageFilter.GaussianBlur(radius=request.shadow_blur))
            shadow = Image.new("RGBA", subject_img.size, (0, 0, 0, 0))
            shadow.putalpha(shadow_alpha)
            sx = center_x + request.shadow_offset_x
            sy = center_y + request.shadow_offset_y
            template_img.alpha_composite(shadow, (sx, sy))

        # Pegar el sujeto encima
        template_img.alpha_composite(subject_img, (center_x, center_y))

        # Guardar resultado
        result_id = f"composite_{datetime.utcnow().timestamp()}"
        result_filename = f"{result_id}.jpg"
        result_path = OUTPUT_DIR / result_filename

        # Convertir a RGB sobre fondo blanco
        rgb_img = Image.new("RGB", template_img.size, (255, 255, 255))
        rgb_img.paste(template_img, mask=template_img.split()[3])

        # Vignette final (si está activo)
        if request.vignette_strength and request.vignette_strength > 0:
            rgb_img = _apply_vignette(rgb_img, request.vignette_strength, request.vignette_size)

        rgb_img.save(result_path, "JPEG", quality=95)

        return {
            "success": True,
            "message": "Composición creada exitosamente",
            "filename": result_filename,
            "filepath": str(result_path),
            "size": (rgb_img.width, rgb_img.height)
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error al componer: {str(e)}")


@app.post("/api/adjust-image")
async def adjust_image(
    request: AdjustmentsRequest,
    
):
    """Aplicar ajustes con Pillow (tiempo real preview)."""
    try:
        # Validar imagen
        image_path = UPLOAD_DIR / request.image_filename
        if not image_path.exists():
            raise HTTPException(status_code=404, detail=f"Imagen no encontrada: {request.image_filename}")

        from PIL import ImageEnhance, ImageFilter

        # Abrir y convertir
        img = Image.open(image_path)
        if img.mode != 'RGBA':
            img = img.convert('RGBA')

        # Aplicar ajustes
        if request.brightness != 1.0:
            enhancer = ImageEnhance.Brightness(img)
            img = enhancer.enhance(request.brightness)

        if request.contrast != 1.0:
            enhancer = ImageEnhance.Contrast(img)
            img = enhancer.enhance(request.contrast)

        if request.saturation != 1.0:
            enhancer = ImageEnhance.Color(img)
            img = enhancer.enhance(request.saturation)

        if request.sharpness != 1.0:
            enhancer = ImageEnhance.Sharpness(img)
            img = enhancer.enhance(request.sharpness)

        if request.blur > 0:
            img = img.filter(ImageFilter.GaussianBlur(radius=request.blur))

        # Temperatura / tinte (white balance simple)
        if request.temperature != 0.0 or request.tint != 0.0:
            img = _apply_temp_tint(img, request.temperature, request.tint)

        # Guardar resultado temporal
        output_id = f"adj_{datetime.utcnow().timestamp()}"
        output_filename = f"{output_id}.png"
        output_path = UPLOAD_DIR / output_filename

        img.save(output_path, "PNG")

        return {
            "success": True,
            "filename": output_filename,
            "width": img.width,
            "height": img.height
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error ajustando imagen: {str(e)}")


@app.post("/api/apply-preset")
async def apply_preset(
    image_filename: str = Query(...),
    preset_id: str = Query(...),
    
):
    """Aplicar un preset de Darktable a una imagen."""
    try:
        # Validar que la imagen exista (puede estar en UPLOAD_DIR o OUTPUT_DIR)
        image_path = UPLOAD_DIR / image_filename
        if not image_path.exists():
            image_path = OUTPUT_DIR / image_filename
        if not image_path.exists():
            raise HTTPException(status_code=404, detail=f"Imagen no encontrada: {image_filename}")

        # Validar que el preset exista
        preset_path = PRESETS_DIR / f"{preset_id}.xmp"
        if not preset_path.exists():
            raise HTTPException(status_code=404, detail=f"Preset no encontrado: {preset_id}")

        # Crear directorio temporal para la salida de darktable
        with tempfile.TemporaryDirectory() as tmpdir:
            # Aplicar preset con darktable-cli
            # Formato: darktable-cli [IMAGE_FILE] [XMP_FILE] OUTPUT_DIR [OPTIONS]
            cmd = [
                "/usr/bin/darktable-cli",
                str(image_path),
                str(preset_path),
                tmpdir
            ]

            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)

            if result.returncode != 0:
                raise Exception(f"Error en Darktable: {result.stderr}")

            # Buscar el archivo generado en el directorio temporal
            output_files = list(Path(tmpdir).glob("*"))
            if not output_files:
                raise Exception("Darktable no generó ningún archivo de salida")

            # Usar el primer archivo generado
            temp_output = output_files[0]

            # Generar nombre del archivo final
            output_id = f"preset_{datetime.utcnow().timestamp()}"
            output_filename = f"{output_id}.jpg"
            output_path = OUTPUT_DIR / output_filename

            # Copiar el resultado a OUTPUT_DIR
            shutil.copy(str(temp_output), str(output_path))

            return {
                "success": True,
                "filename": output_filename,
                "message": f"Preset '{preset_id}' aplicado exitosamente"
            }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error aplicando preset: {str(e)}")


@app.get("/api/download/{filename}")
async def download_image(
    filename: str,
    
):
    """Descargar imagen procesada."""
    # Validar que el archivo esté en un directorio permitido
    filepath = OUTPUT_DIR / filename
    if not filepath.exists():
        filepath = UPLOAD_DIR / filename

    if not filepath.exists():
        raise HTTPException(status_code=404, detail="Archivo no encontrado")

    # Validar que no haya path traversal
    if not str(filepath).startswith(str(UPLOAD_DIR)) and not str(filepath).startswith(str(OUTPUT_DIR)):
        raise HTTPException(status_code=403, detail="Acceso denegado")

    return FileResponse(filepath, filename=filename)


@app.get("/")
async def root():
    """Página principal de Darkroom."""
    return FileResponse("static/index.html")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8001)
