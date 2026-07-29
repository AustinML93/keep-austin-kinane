# Debian slim, NOT alpine — deliberately, and against the DawgHaus habit.
#
# pywebpush pulls in `cryptography`, which is part Rust. On alpine (musl) pip
# can fall through to building it from source, which needs gcc + a Rust
# toolchain + openssl headers that this image doesn't have — a long, ugly
# failure on first deploy. Debian gets a prebuilt manylinux wheel every time.
#
# DawgHaus runs stock alpine images because it installs nothing. The moment a
# project has real dependencies that rule stops paying for itself. ~40MB more
# image for a build that just works.
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . /app/api
ENV PYTHONPATH=/app
ENV KAK_DB=/data/kak.sqlite3
ENV PYTHONUNBUFFERED=1

# The DB lives on a bind mount; the image is disposable.
VOLUME ["/data"]

EXPOSE 8000
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
