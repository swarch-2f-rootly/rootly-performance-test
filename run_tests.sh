#!/bin/bash
# Script para ejecutar las pruebas de rendimiento de Rootly

echo "=================================================="
echo "   Rootly Performance Testing Suite"
echo "=================================================="
echo ""

# Verificar si Python está instalado
if ! command -v python3 &> /dev/null; then
    echo "ERROR: Python3 no está instalado"
    exit 1
fi

# Verificar si existe el directorio virtual
if [ ! -d "venv" ]; then
    echo "Creando entorno virtual..."
    python3 -m venv venv
fi

# Activar entorno virtual
echo "Activando entorno virtual..."
source venv/bin/activate

# Instalar dependencias
echo "Instalando dependencias..."
pip install -q --upgrade pip
pip install -q -r requirements.txt

echo ""
echo "=================================================="
echo "   Configuración"
echo "=================================================="
echo ""

# Verificar si existe archivo de configuración
if [ ! -f "config.json" ]; then
    echo "WARNING: Archivo config.json no encontrado"
    echo "Creando archivo de configuración por defecto..."
    echo "IMPORTANTE: Edita config.json con la IP de tu máquina A"
    exit 1
fi

# Mostrar configuración
echo "Archivo de configuración: config.json"
BASE_URL=$(python3 -c "import json; print(json.load(open('config.json'))['base_url'])")
echo "URL Base: $BASE_URL"
echo ""

# Confirmar ejecución
read -p "¿Iniciar pruebas de rendimiento? (s/n): " -n 1 -r
echo ""

if [[ ! $REPLY =~ ^[Ss]$ ]]; then
    echo "Pruebas canceladas"
    exit 0
fi

echo ""
echo "=================================================="
echo "   Ejecutando Pruebas"
echo "=================================================="
echo ""

# Ejecutar pruebas
python3 performance_test.py config.json

# Verificar si la ejecución fue exitosa
if [ $? -eq 0 ]; then
    echo ""
    echo "=================================================="
    echo "   Pruebas Completadas Exitosamente"
    echo "=================================================="
    echo ""
    
    # Encontrar el directorio de resultados más reciente
    RESULTS_DIR=$(ls -td results_* 2>/dev/null | head -1)
    
    if [ -n "$RESULTS_DIR" ]; then
        echo "Resultados guardados en: $RESULTS_DIR"
        echo ""
        echo "Archivos generados:"
        ls -lh "$RESULTS_DIR"
        echo ""
        echo "Tip: Abre las imágenes .png para ver las gráficas"
        echo "Tip: Abre summary_report.txt para ver el resumen"
    fi
else
    echo ""
    echo "ERROR durante la ejecución de las pruebas"
    exit 1
fi

# Desactivar entorno virtual
deactivate
