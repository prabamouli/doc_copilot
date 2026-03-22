#!/bin/sh
set -eu

MODEL_LIST="${OLLAMA_MODELS:-llama3.2:1b}"

ollama serve &
OLLAMA_PID=$!

sleep 5

for model in $(echo "$MODEL_LIST" | tr ',' ' '); do
  echo "Pulling Ollama model: $model"
  ollama pull "$model"
done

wait "$OLLAMA_PID"
