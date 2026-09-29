FROM python:3.14-slim

ARG USER=app
ARG APP_DIR=/app
ENV APP_DIR=${APP_DIR}

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    wget \
    curl \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Install dockerize
ENV DOCKERIZE_VERSION v0.6.1
RUN wget https://github.com/jwilder/dockerize/releases/download/$DOCKERIZE_VERSION/dockerize-linux-amd64-$DOCKERIZE_VERSION.tar.gz \
    && tar -C /usr/local/bin -xzvf dockerize-linux-amd64-$DOCKERIZE_VERSION.tar.gz \
    && rm dockerize-linux-amd64-$DOCKERIZE_VERSION.tar.gz

# Create user and home directory
RUN groupadd -g 61000 ${USER} \
  && useradd -g 61000 -u 61000 -ms /bin/bash -d ${APP_DIR} ${USER}

WORKDIR ${APP_DIR}

# Copy requirements.txt
COPY requirements.txt ./

# Install uv and use it to install requirements
RUN pip install --no-cache-dir uv \
    && uv pip install --system -r requirements.txt

# Copy project files
COPY . .

# Make scripts executable
RUN chmod +x /app/scripts/*.sh

# Set permissions for the /app directory
RUN chown -R ${USER}:${USER} ${APP_DIR}

# Switch to non-root user
USER ${USER}

# Expose the port the app runs on
EXPOSE 8080

# The commit this image was built from, served by GET /_/version so a deploy can be
# verified against the process actually answering requests, not only against Koyeb's
# record - which can be correct while a stale sleep snapshot serves older code.
# Build with: --build-arg GIT_SHA=$(git rev-parse HEAD)
ARG GIT_SHA=unknown
ENV GIT_SHA=${GIT_SHA}

ENTRYPOINT ["/app/scripts/entrypoint.sh"]
CMD ["/app/scripts/start_server.sh"]
