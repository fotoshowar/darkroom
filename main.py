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
from pathlib import Path
from typing import Optional
from datetime import datetime, timedelta

from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, BackgroundTasks, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, text
from PIL import Image
from rembg import remove

from database import get_db
from models import Photographer

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

# Usuario autorizado (solo fotoshowonlinear@gmail.com puede usar Darkroom)
AUTHORIZED_EMAIL = "fotoshowonlinear@gmail.com"

# Estado de trabajos (en memoria para MVP, luego mover a Redis)
jobs = {}

# ── Schemas ─────────────────────────────────────────────────────────────────────

class PresetUploadResponse(BaseModel):
    preset_id: str
    filename: str
    message: str

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

class AdjustmentsRequest(BaseModel):
    """Ajustes con Pillow para previsualizacion"""
    image_filename: str
    brightness: float = 1.0      # 0.5 = 50% más oscuro, 1.5 = 50% más claro
    contrast: float = 1.0        # 0.5 a 2.0
    saturation: float = 1.0      # 0.0 = B&N, 2.0 = muy saturado
    blur: float = 0.0            # 0.0 a 10.0
    sharpness: float = 1.0       # 0.0 a 2.0

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

async def check_darkroom_access(db: AsyncSession = Depends(get_db)):
    """Verificar que el usuario es fotoshowonlinear@gmail.com."""
    # TODO: Verificar token y email del fotógrafo
    # Por ahora, retornamos que está autorizado (MVP)
    return True

# ── Endpoints ───────────────────────────────────────────────────────────────────

@app.get("/api/info")
async def info():
    """Información del servicio."""
    return {
        "service": "Darkroom - Darktable Editing Service",
        "status": "running",
        "version": "1.0.0",
        "authorized_user": AUTHORIZED_EMAIL
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
    file: UploadFile = File(...),
    authorized: bool = Depends(check_darkroom_access)
):
    """Subir un preset de Darktable (.xmp)."""
    if not authorized:
        raise HTTPException(status_code=403, detail="No autorizado para usar Darkroom")

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


@app.get("/api/presets/")
async def list_presets(authorized: bool = Depends(check_darkroom_access)):
    """Listar todos los presets disponibles."""
    if not authorized:
        raise HTTPException(status_code=403, detail="No autorizado para usar Darkroom")

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
    authorized: bool = Depends(check_darkroom_access),
    db: AsyncSession = Depends(get_db)
):
    """Procesar fotos con un preset de Darktable."""
    if not authorized:
        raise HTTPException(status_code=403, detail="No autorizado para usar Darkroom")

    # Verificar que el preset existe
    preset_path = PRESETS_DIR / f"{request.preset_id}.xmp"
    if not preset_path.exists():
        raise HTTPException(status_code=404, detail="Preset no encontrado")

    # Crear trabajo
    job_id = f"job_{datetime.utcnow().timestamp()}"
    jobs[job_id] = {
        "status": "pending",
        "progress": 0.0,
        "total_photos": len(request.photo_ids),
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
        request.photo_ids,
        db
    )

    return ProcessResponse(
        job_id=job_id,
        status="pending",
        message="Procesamiento iniciado"
    )


async def process_photos_task(
    job_id: str,
    preset_id: str,
    photo_ids: list[int],
    db: AsyncSession
):
    """Tarea de background para procesar fotos con Darktable."""
    try:
        jobs[job_id]["status"] = "processing"
        preset_path = PRESETS_DIR / f"{preset_id}.xmp"

        # Obtener fotos de la DB de FotoShow
        from models import Photo
        result = await db.execute(
            select(Photo).where(Photo.id.in_(photo_ids))
        )
        photos = result.scalars().all()

        jobs[job_id]["total_photos"] = len(photos)

        # Directorio de salida para este trabajo
        job_output_dir = OUTPUT_DIR / job_id
        job_output_dir.mkdir(exist_ok=True)

        # Procesar cada foto
        for i, photo in enumerate(photos):
            try:
                # Descargar la foto original desde R2
                # TODO: Implementar descarga desde R2
                # Por ahora, usamos un archivo de prueba si no hay s3_key

                # Aplicar Darktable con el preset
                output_file = job_output_dir / f"edited_{photo.id}.jpg"

                # Comando darktable-cli
                cmd = [
                    "/usr/bin/darktable-cli",
                    f"--conf plugins/imageio/format/jpeg/quality=95",
                    f"--output {output_file}",
                    "--width 2048", "--height 2048",
                    f"--apply-custom-profile {preset_path}",
                    "/tmp/test_input.jpg"  # TODO: Reemplazar con la foto real
                ]

                # Ejecutar Darktable
                result = subprocess.run(cmd, capture_output=True, text=True)

                if result.returncode == 0:
                    jobs[job_id]["processed_photos"] += 1
                    jobs[job_id]["progress"] = (i + 1) / len(photos) * 100
                    # TODO: Subir foto editada a R2
                else:
                    print(f"Error procesando foto {photo.id}: {result.stderr}")

            except Exception as e:
                print(f"Error procesando foto {photo.id}: {e}")

        jobs[job_id]["status"] = "completed"
        jobs[job_id]["message"] = "Procesamiento completado"

    except Exception as e:
        jobs[job_id]["status"] = "failed"
        jobs[job_id]["message"] = f"Error: {str(e)}"


@app.get("/api/jobs/{job_id}", response_model=JobResponse)
async def get_job(job_id: str, authorized: bool = Depends(check_darkroom_access)):
    """Obtener estado de un trabajo."""
    if not authorized:
        raise HTTPException(status_code=403, detail="No autorizado para usar Darkroom")

    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Trabajo no encontrado")

    return JobResponse(**jobs[job_id])


@app.get("/api/jobs/")
async def list_jobs(authorized: bool = Depends(check_darkroom_access)):
    """Listar todos los trabajos."""
    if not authorized:
        raise HTTPException(status_code=403, detail="No autorizado para usar Darkroom")

    return jobs


# ── NUEVOS ENDPOINTS: Extracción de fondo + Composición ──────────────────

@app.post("/api/upload-image")
async def upload_image(
    file: UploadFile = File(...),
    authorized: bool = Depends(check_darkroom_access)
):
    """Subir una imagen (sujeto o plantilla)."""
    if not authorized:
        raise HTTPException(status_code=403, detail="No autorizado para usar Darkroom")

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
    authorized: bool = Depends(check_darkroom_access)
):
    """Extraer el fondo de una imagen usando rembg."""
    if not authorized:
        raise HTTPException(status_code=403, detail="No autorizado para usar Darkroom")

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


@app.post("/api/composite", response_model=dict)
async def composite_images(
    request: CompositeRequest,
    authorized: bool = Depends(check_darkroom_access)
):
    """Componer imagen de sujeto (sin fondo) sobre plantilla usando filenames guardados.

    Args:
        subject_filename: Filename del sujeto sin fondo
        template_filename: Filename de la plantilla
        scale_percent: Escala en porcentaje (100 = tamaño original)
        offset_x: Offset X desde el centro en píxeles
        offset_y: Offset Y desde el centro en píxeles
    """
    if not authorized:
        raise HTTPException(status_code=403, detail="No autorizado para usar Darkroom")

    try:
        # Validar que los archivos existan
        subject_path = UPLOAD_DIR / request.subject_filename
        template_path = UPLOAD_DIR / request.template_filename

        if not subject_path.exists():
            raise HTTPException(status_code=404, detail=f"Archivo de sujeto no encontrado: {request.subject_filename}")
        if not template_path.exists():
            raise HTTPException(status_code=404, detail=f"Archivo de plantilla no encontrado: {request.template_filename}")

        # Leer imágenes desde archivos guardados
        subject_img = Image.open(subject_path).convert("RGBA")
        template_img = Image.open(template_path).convert("RGB")

        # Calcular nuevo tamaño basado en porcentaje
        new_width = int(subject_img.width * request.scale_percent / 100)
        new_height = int(subject_img.height * request.scale_percent / 100)

        # Redimensionar sujeto
        if new_width > 0 and new_height > 0:
            subject_img = subject_img.resize((new_width, new_height), Image.LANCZOS)

        # Calcular posición centrada + offsets
        center_x = (template_img.width - new_width) // 2 + request.offset_x
        center_y = (template_img.height - new_height) // 2 + request.offset_y

        # Asegurar que la posición esté dentro de límites válidos
        center_x = max(0, min(center_x, template_img.width - new_width))
        center_y = max(0, min(center_y, template_img.height - new_height))

        # Componer sobre plantilla
        template_img.paste(subject_img, (center_x, center_y), subject_img)

        # Guardar resultado
        result_id = f"composite_{datetime.utcnow().timestamp()}"
        result_filename = f"{result_id}.jpg"
        result_path = OUTPUT_DIR / result_filename

        # Convertir a RGB y guardar como JPEG
        rgb_img = Image.new("RGB", template_img.size, (255, 255, 255))
        rgb_img.paste(template_img, (0, 0))
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
    authorized: bool = Depends(check_darkroom_access)
):
    """Aplicar ajustes con Pillow (tiempo real preview)."""
    if not authorized:
        raise HTTPException(status_code=403, detail="No autorizado para usar Darkroom")

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
    authorized: bool = Depends(check_darkroom_access)
):
    """Aplicar un preset de Darktable a una imagen."""
    if not authorized:
        raise HTTPException(status_code=403, detail="No autorizado para usar Darkroom")

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
    authorized: bool = Depends(check_darkroom_access)
):
    """Descargar imagen procesada."""
    if not authorized:
        raise HTTPException(status_code=403, detail="No autorizado para usar Darkroom")

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
