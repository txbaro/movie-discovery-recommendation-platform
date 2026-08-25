# --- Stage 1: Build dependencies ---
# Dùng multi-stage build: cài đặt package ở stage riêng, rồi copy sang image
# cuối cùng — giúp image production nhẹ hơn (không mang theo compiler/build tools).
FROM python:3.12-slim AS builder

WORKDIR /app

# Cài các gói hệ thống cần thiết để build một số thư viện Python có C extension
# (vd asyncpg, pandas, scikit-learn cần compiler lúc build từ source)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
# Cài vào thư mục riêng (--user) để dễ copy sang stage sau
RUN pip install --no-cache-dir --user -r requirements.txt


# --- Stage 2: Runtime image (nhẹ, không có build tools) ---
FROM python:3.12-slim

WORKDIR /app

# libpq5 là thư viện runtime PostgreSQL client mà asyncpg cần —
# nhẹ hơn nhiều so với libpq-dev (chỉ cần lúc build, không cần lúc chạy)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    && rm -rf /var/lib/apt/lists/*

# Tạo user thường TRƯỚC khi copy package — để có thể chown đúng chủ sở hữu
# ngay lúc copy, tránh việc package nằm dưới quyền root trong khi app chạy
# bằng user khác (đây chính là nguyên nhân lỗi "Permission denied" lúc trước).
RUN useradd --create-home appuser

# Copy package đã cài từ stage builder, gán quyền sở hữu cho appuser luôn.
# Lưu ý: đường dẫn đích đổi từ /root/.local sang /home/appuser/.local vì
# appuser không có quyền truy cập thư mục home của root.
COPY --from=builder --chown=appuser:appuser /root/.local /home/appuser/.local
ENV PATH=/home/appuser/.local/bin:$PATH

# Copy source code, cũng gán quyền sở hữu cho appuser
COPY --chown=appuser:appuser ./app ./app
COPY --chown=appuser:appuser ./alembic ./alembic
COPY --chown=appuser:appuser alembic.ini .
RUN chmod +x /app/app/scripts/start_render.sh

# Chạy bằng user không phải root — thực hành bảo mật cơ bản,
# tránh chạy app với quyền root trong container
USER appuser

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
