# Darkroom - Darktable Editing Service

## 🌿 Estrategia de Branching

### Flujo GitFlow simplificado

```
main (producción)
    ↑
    │ merge (deploy)
    │
develop (desarrollo)
    ↑
    │ feature branches
```

### Reglas:

- **`main`** → Producción (solo código probado y estable)
- **`develop`** → Desarrollo (feature branches se mergen acá)
- **Feature branches** → Funcionalidades específicas (ej: `feature/modal-edicion`)

### Workflow:

1. **Nueva funcionalidad:**
   ```bash
   git checkout develop
   git checkout -b feature/nombre-funcionalidad
   # Desarrollar
   git checkout develop
   git merge feature/nombre-funcionalidad
   git branch -d feature/nombre-funcionalidad
   ```

2. **Deploy a producción:**
   ```bash
   git checkout main
   git merge develop
   # Deploy main
   ```

3. **Hotfix en producción:**
   ```bash
   git checkout main
   git checkout -b hotfix/corregencia
   # Corregir
   git checkout main
   git merge hotfix/corregencia
   git checkout develop
   git merge main
   ```

---

## 📦 Descripción

Servicio separado para edición automática de fotos con Darktable.

### Características:

- Edición en lote con presets de Darktable
- Soporte para fotos RAW y JPEG
- Procesamiento asíncrono
- Solo accesible para `fotoshowonlinear@gmail.com`
- Subdominio: `darkroom.fotoshow.online`

---

## 🚀 Instalación

```bash
# Clonar el repo
git clone https://github.com/fotoshowar/darkroom.git
cd darkroom

# Crear entorno virtual
python3 -m venv venv
source venv/bin/activate

# Instalar dependencias
pip install -r requirements.txt

# Instalar Darktable
apt update && apt install -y darktable
```

---

## 🔧 Configuración

### Dependencias:
- FastAPI
- Darktable CLI (4.6.1+)
- PostgreSQL (compartido con FotoShow)
- R2 Storage (compartido con FotoShow)

### Archivos de configuración:
- `/etc/nginx/sites-available/darkroom` - Nginx
- `/etc/systemd/system/darkroom.service` - Systemd service

---

## 🌐 Endpoints API

- `GET /` → Página principal HTML (interfaz)
- `GET /api/info` → Información del servicio (JSON)
- `GET /api/status` → Estado del servicio (Darktable OK)
- `POST /api/presets/upload` → Subir preset (.xmp)
- `GET /api/presets/` → Listar presets
- `POST /api/process` → Procesar fotos con preset
- `GET /api/jobs/{job_id}` → Estado del trabajo
- `GET /api/jobs/` → Lista de trabajos

---

## 👥 Autorización

Solo `fotoshowonlinear@gmail.com` puede usar Darkroom.

TODO: Implementar autenticación JWT real.

---

## 📊 Estado actual (develop)

✅ Completado:
- Servicio FastAPI básico
- Frontend HTML/Bootstrap
- Darktable CLI integrado
- Nginx + HTTPS (Let's Encrypt)
- Sistema de presets (.xmp)
- Procesamiento en background

⏳ En desarrollo:
- Modal de edición en FotoShow
- Preview rápido con Pillow
- Exportación final con Darktable
- Autenticación JWT real
- Integración completa con FotoShow

---

## 🔖 Commits

- `71dd99b` - fix: return HTML on root path instead of JSON
- `f9a1a94` - feat: initial implementation of Darkroom service

---

## 📞 Contacto

- Repositorio: https://github.com/fotoshowar/darkroom
- Email: soporte@fotoshow.online
