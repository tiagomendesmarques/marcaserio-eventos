# Versão online (Cloud Run): servidor + interface, dados no Firestore.
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY marcaserio_checkin.py store_sqlite.py store_firestore.py ./
COPY web ./web
RUN useradd --system --no-create-home app
USER app
CMD ["python", "marcaserio_checkin.py"]
