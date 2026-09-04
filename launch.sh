#!/usr/bin/env bash
# Script d'amorceur pour NeoDisk Manager Pro

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Détection de l'environnement virtuel Python
PYTHON_BIN=""

if [ -f "$SCRIPT_DIR/venv/bin/python3" ]; then
    PYTHON_BIN="$SCRIPT_DIR/venv/bin/python3"
elif [ -f "$SCRIPT_DIR/myvenv/bin/python3" ]; then
    PYTHON_BIN="$SCRIPT_DIR/myvenv/bin/python3"
elif command -v python3 &>/dev/null; then
    PYTHON_BIN="$(command -v python3)"
else
    echo "❌ Erreur: Python3 est introuvable."
    exit 1
fi

# Vérification de l'installation de Flask
"$PYTHON_BIN" -c "import flask" 2>/dev/null
if [ $? -ne 0 ]; then
    echo "⚠️ Flask n'est pas installé dans $PYTHON_BIN. Tentative d'installation..."
    "$PYTHON_BIN" -m pip install flask
fi

# Si l'utilisateur exécute sans privilèges root et que pkexec est présent, proposer l'élévation pour accès disque physique
if [ "$EUID" -ne 0 ]; then
    if [ "$1" == "--root" ] || [ "$1" == "-r" ]; then
        if command -v pkexec &>/dev/null; then
            exec pkexec env DISPLAY="$DISPLAY" XAUTHORITY="$XAUTHORITY" "$PYTHON_BIN" "$SCRIPT_DIR/app.py"
        elif command -v sudo &>/dev/null; then
            exec sudo "$PYTHON_BIN" "$SCRIPT_DIR/app.py"
        fi
    fi
fi

# Démarrage standard
echo "🚀 Lancement de NeoDisk Manager Pro..."
exec "$PYTHON_BIN" "$SCRIPT_DIR/app.py" "$@"
