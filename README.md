# Darkroom - Servicio de Edición con Darktable

Servicio separado para edición automática de fotos con Darktable.

## Características

- Edición en lote con presets de Darktable
- Soporte para fotos RAW y JPEG
- Procesamiento asíncrono
- Solo accesible para fotoshowonlinear@gmail.com
- Subdominio: darkroom.fotoshow.online

## Stack

- FastAPI
- Darktable CLI
- PostgreSQL (compartido con FotoShow)
- R2 Storage (compartido con FotoShow)

## Endpoints

- `POST /api/presets/upload` - Subir preset (.xmp)
- `GET /api/presets/` - Listar presets
- `POST /api/process` - Procesar fotos con preset
- `GET /api/jobs/{job_id}` - Estado del trabajo
- `GET /api/jobs/` - Lista de trabajos

## Instalación Darktable

```bash
apt update && apt install -y darktable
```
