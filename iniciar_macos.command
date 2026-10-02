#!/bin/bash
set -e
cd "$(dirname "$0")"

if [ ! -x ".venv/bin/python" ]; then
    echo "Preparando o ambiente..."
    python3 -m venv .venv
    .venv/bin/python -m pip install --upgrade pip
    .venv/bin/pip install -r requirements.txt
fi

echo "Abrindo EPUB em voz..."
.venv/bin/python -m streamlit run app.py
