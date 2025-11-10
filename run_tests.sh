#!/bin/bash
# Script mejorado para ejecutar las pruebas de rendimiento de Rootly

echo "=================================================="
echo "   Rootly Performance Testing Suite (v2)"
echo "=================================================="
echo ""

# Configurar variables de entorno para matplotlib
export MPLBACKEND=Agg
export DISPLAY=""

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

# Opciones adicionales
echo ""
echo "Opciones disponibles:"
echo "1. Ejecutar pruebas normales"
echo "2. Ejecutar pruebas sin gráficos (solo CSV)"
echo ""
read -p "Selecciona una opción (1-2): " -n 1 -r
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

# Configurar timeout para evitar cuelgues
TIMEOUT_SECONDS=3600  # 1 hora

# Función para manejar timeout
timeout_handler() {
    echo ""
    echo "ERROR: Las pruebas han excedido el tiempo límite de $TIMEOUT_SECONDS segundos"
    echo "Terminando proceso..."
    exit 124
}

# Configurar trap para timeout
trap timeout_handler SIGALRM

# Iniciar timeout
(sleep $TIMEOUT_SECONDS && kill -ALRM $$) &
TIMEOUT_PID=$!

# Ejecutar pruebas con manejo de errores
set -e
trap 'echo ""; echo "ERROR: Las pruebas fallaron inesperadamente"; kill $TIMEOUT_PID 2>/dev/null; exit 1' ERR

echo "Iniciando pruebas..."
echo "PID del timeout: $TIMEOUT_PID"
echo "Configuración matplotlib: MPLBACKEND=$MPLBACKEND"
echo ""

# Ejecutar el script principal
python3 performance_test.py config.json

# Si llegamos aquí, las pruebas fueron exitosas
kill $TIMEOUT_PID 2>/dev/null || true

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
    ls -la "$RESULTS_DIR"
    echo ""
    
    # Verificar si se generaron gráficos
    if ls "$RESULTS_DIR"/*.png 1> /dev/null 2>&1; then
        echo "✓ Gráficos generados correctamente"
    else
        echo "⚠ No se generaron gráficos (solo datos CSV disponibles)"
    fi
    
    # Verificar si existe el reporte de resumen
    if [ -f "$RESULTS_DIR/summary_report.txt" ]; then
        echo "✓ Reporte de resumen disponible"
        echo ""
        echo "Resumen de resultados:"
        echo "======================================"
        head -20 "$RESULTS_DIR/summary_report.txt"
        echo ""
        echo "Ver archivo completo: $RESULTS_DIR/summary_report.txt"
    fi
else
    echo "WARNING: No se encontraron directorios de resultados"
fi

echo ""
echo "=================================================="
echo "   Limpieza"
echo "=================================================="
echo ""

# Limpiar procesos huérfanos de matplotlib si existen
pkill -f "matplotlib" 2>/dev/null || true

echo "Pruebas finalizadas exitosamente"
echo ""