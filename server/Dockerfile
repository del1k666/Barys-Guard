# Образ сервера BarysGuard. Используется стендом (deploy/stand); боевая
# упаковка — отдельный подпроект.
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY pyproject.toml ./
COPY barysguard ./barysguard
COPY alembic.ini ./
COPY alembic ./alembic

# Не editable: код копируется в site-packages, каталог /app нужен только
# миграциям. Внимание: каталог alembic/ рядом с alembic.ini затеняет
# одноимённую библиотеку, если запускать python из /app с этим каталогом в
# sys.path. Консольные скрипты (alembic, uvicorn) на это не реагируют: их
# sys.path[0] — каталог скрипта.
# greenlet нужен sqlalchemy.ext.asyncio, но не объявлен в pyproject (локально
# приходит транзитивно через dev-зависимости), поэтому ставим явно.
RUN pip install . greenlet

RUN useradd --system --uid 10001 --home-dir /app barysguard \
    && mkdir -p /var/lib/barysguard/pki /var/lib/barysguard/artifacts /tls /enroll \
    && chown -R barysguard:barysguard /var/lib/barysguard /tls /enroll /app

USER barysguard

EXPOSE 8000

CMD ["uvicorn", "barysguard.main:app", "--host", "0.0.0.0", "--port", "8000"]
