FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    STORAGE_ROOT=/data/generated

# libraqm → real HarfBuzz shaping for Persian in Pillow (hard requirement)
RUN apt-get update && apt-get install -y --no-install-recommends \
        libraqm0 libfribidi0 libharfbuzz0b \
        libjpeg62-turbo libfreetype6 libpng16-16 \
        fonts-dejavu-core ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Chromium for the HTML renderer. If this layer is removed the bot still works:
# the renderer factory falls back to Pillow automatically.
RUN python -m playwright install --with-deps chromium

COPY . .

# Build-time verification: fail the image, not production.
RUN python - <<'PY'
from PIL import features
from pathlib import Path
assert features.check("raqm"), "Pillow is missing libraqm — Persian shaping would break"
for f in ("Vazirmatn-Regular.ttf", "Vazirmatn-Medium.ttf", "Vazirmatn-Bold.ttf"):
    assert Path("assets/fonts", f).exists(), f"missing font {f}"
assert Path("assets/templates/weekly_plan_v1.png").exists(), "missing template asset"
import sys; sys.path.insert(0, ".")
from app.rendering.html_renderer import HtmlRenderer
print("raqm ok · assets ok · chromium:", HtmlRenderer.available())
PY

RUN mkdir -p /data/generated && chmod +x docker-entrypoint.sh

HEALTHCHECK --interval=60s --timeout=10s --start-period=40s --retries=3 \
    CMD python -c "import os,urllib.request,sys; p=os.getenv('PORT'); sys.exit(0) if not p else urllib.request.urlopen(f'http://127.0.0.1:{p}/health', timeout=5)"

ENTRYPOINT ["./docker-entrypoint.sh"]
CMD ["bot"]
