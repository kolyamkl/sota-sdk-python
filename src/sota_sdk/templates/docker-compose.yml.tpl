services:
  {{AGENT_NAME}}:
    build: .
    env_file: .env
    restart: unless-stopped
