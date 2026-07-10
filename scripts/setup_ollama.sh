#!/usr/bin/env bash
# Host-side Ollama setup for the PromptETL single-image deployment.
#
# Detects the machine's RAM, picks the largest qwen2.5-coder model that fits
# comfortably, pulls it, and prints the docker run flag to use. Ollama runs
# natively on the host (Metal/CUDA acceleration where available); the
# container reaches it via host.docker.internal.
#
# Usage:  ./scripts/setup_ollama.sh
set -euo pipefail

bold() { printf '\033[1m%s\033[0m\n' "$*"; }

# ---------------------------------------------------------------------------
# 1. Detect host RAM (GiB)
# ---------------------------------------------------------------------------
OS=$(uname -s)
case "$OS" in
  Darwin) RAM_BYTES=$(sysctl -n hw.memsize) ;;
  Linux)  RAM_BYTES=$(awk '/MemTotal:/ {print $2 * 1024; exit}' /proc/meminfo) ;;
  *)
    echo "Unsupported OS: $OS"
    echo "On Windows, run this script inside WSL2, or install Ollama from"
    echo "https://ollama.com/download and pull a model manually."
    exit 1
    ;;
esac
RAM_GB=$((RAM_BYTES / 1073741824))
bold "Detected ${RAM_GB} GiB RAM on $OS"

# ---------------------------------------------------------------------------
# 2. Pick a model tier
#    7b  needs ~6 GiB free | 14b ~10 GiB | 32b ~20 GiB (Q4 quantization)
# ---------------------------------------------------------------------------
if   [ "$RAM_GB" -ge 28 ]; then MODEL="qwen2.5-coder:32b"
elif [ "$RAM_GB" -ge 14 ]; then MODEL="qwen2.5-coder:14b"
else                            MODEL="qwen2.5-coder:7b"
fi
bold "Selected model: $MODEL"

# ---------------------------------------------------------------------------
# 3. Ensure Ollama is installed and running
# ---------------------------------------------------------------------------
if ! command -v ollama >/dev/null 2>&1; then
  echo "Ollama is not installed."
  case "$OS" in
    Darwin) echo "Install with:  brew install ollama   (or https://ollama.com/download)" ;;
    Linux)  echo "Install with:  curl -fsSL https://ollama.com/install.sh | sh" ;;
  esac
  exit 1
fi

if ! curl -fsS http://localhost:11434/api/tags >/dev/null 2>&1; then
  echo "Starting Ollama server..."
  if [ "$OS" = "Darwin" ]; then
    (ollama serve >/dev/null 2>&1 &)
  else
    (command -v systemctl >/dev/null && sudo systemctl start ollama) || (ollama serve >/dev/null 2>&1 &)
  fi
  for _ in $(seq 1 20); do
    curl -fsS http://localhost:11434/api/tags >/dev/null 2>&1 && break
    sleep 1
  done
fi

# ---------------------------------------------------------------------------
# 4. Pull the model
# ---------------------------------------------------------------------------
bold "Pulling $MODEL (skips if already present)..."
ollama pull "$MODEL"

# ---------------------------------------------------------------------------
# 5. Print the wiring for the app container
# ---------------------------------------------------------------------------
echo
bold "Done. Start (or restart) the PromptETL container with:"
cat <<EOF

  docker run -d --name prompt-etl \\
    -p 8080:8080 -v prompt-etl-data:/data \\
    --add-host=host.docker.internal:host-gateway \\
    -e OLLAMA_MODEL=$MODEL \\
    prompt-etl:latest

EOF
echo "(--add-host is a no-op on Docker Desktop for Mac/Windows but required on Linux.)"
