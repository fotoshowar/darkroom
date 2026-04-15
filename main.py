"""
Darkroom - Darktable Editing Service
Servicio separado para edición automática de fotos con Darktable.
"""
import os
import shutil
import subprocess
import asyncio
import tempfile
from pathlib import Path
from typing import Optional
from datetime import datetime, timedelta

from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, BackgroundTasks
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, text

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


@app.get("/")
async def root():
    """Página principal de Darkroom."""
    return FileResponse("static/index.html")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8001)
