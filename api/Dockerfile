FROM python:3.12-alpine

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . /app/api
ENV PYTHONPATH=/app
ENV KAK_DB=/data/kak.sqlite3

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
