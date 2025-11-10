#!/usr/bin/env python3
"""
Script para validar la configuración del sistema de pruebas de rendimiento
"""

import sys
import json
import asyncio
import aiohttp
from pathlib import Path

def validate_config():
    """Valida el archivo de configuración"""
    config_file = Path("config.json")
    
    if not config_file.exists():
        print("ERROR: config.json no encontrado")
        return False
    
    try:
        with open(config_file, 'r') as f:
            config = json.load(f)
        
        # Validar campos requeridos
        required_fields = ['base_url', 'timeout_seconds', 'user_counts', 'endpoints']
        for field in required_fields:
            if field not in config:
                print(f"ERROR: Campo '{field}' faltante en config.json")
                return False
        
        # Validar base_url
        if not config['base_url'].startswith('http'):
            print("ERROR: base_url debe comenzar con http:// o https://")
            return False
        
        # Validar user_counts
        if not isinstance(config['user_counts'], list) or len(config['user_counts']) == 0:
            print("ERROR: user_counts debe ser una lista no vacía")
            return False
        
        # Validar endpoints
        if not isinstance(config['endpoints'], list) or len(config['endpoints']) == 0:
            print("ERROR: endpoints debe ser una lista no vacía")
            return False
        
        for endpoint in config['endpoints']:
            required_endpoint_fields = ['name', 'method', 'path', 'port']
            for field in required_endpoint_fields:
                if field not in endpoint:
                    print(f"ERROR: Campo '{field}' faltante en endpoint")
                    return False
        
        # Validar credenciales de autenticación (opcional pero recomendado)
        if 'auth_credentials' in config:
            auth_creds = config['auth_credentials']
            required_auth_fields = ['email', 'password', 'login_endpoint', 'login_port']
            for field in required_auth_fields:
                if field not in auth_creds:
                    print(f"WARNING: Campo '{field}' faltante en auth_credentials")
        
        print("OK Configuración válida")
        print(f"  - Base URL: {config['base_url']}")
        print(f"  - Endpoints: {len(config['endpoints'])}")
        print(f"  - Niveles de carga: {len(config['user_counts'])}")
        print(f"  - Rango de usuarios: {min(config['user_counts'])} - {max(config['user_counts'])}")
        
        if 'auth_credentials' in config:
            print(f"  - Autenticación: Configurada ({config['auth_credentials']['email']})")
        else:
            print(f"  - Autenticación: No configurada")
        
        return True
        
    except json.JSONDecodeError as e:
        print(f"ERROR: config.json no es un JSON válido: {e}")
        return False
    except Exception as e:
        print(f"ERROR: {e}")
        return False

def validate_dependencies():
    """Valida que las dependencias estén instaladas"""
    print("\nValidando dependencias...")
    
    required_modules = {
        'aiohttp': 'aiohttp',
        'numpy': 'numpy',
        'matplotlib': 'matplotlib'
    }
    
    missing = []
    for module_name, import_name in required_modules.items():
        try:
            __import__(import_name)
            print(f"  OK {module_name}")
        except ImportError:
            print(f"  ERROR {module_name} (faltante)")
            missing.append(module_name)
    
    if missing:
        print(f"\nERROR: Dependencias faltantes: {', '.join(missing)}")
        print("Ejecuta: pip install -r requirements.txt")
        return False
    
    return True

async def validate_authentication(config):
    """Valida que la autenticación funcione"""
    if 'auth_credentials' not in config:
        print("\nWARNING: No hay credenciales de autenticación configuradas")
        return True
    
    print("\nValidando autenticación...")
    
    auth_creds = config['auth_credentials']
    login_url = f"{config['base_url']}:{auth_creds['login_port']}{auth_creds['login_endpoint']}"
    
    print(f"  URL de login: {login_url}")
    print(f"  Usuario: {auth_creds['email']}")
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                login_url,
                json={
                    "email": auth_creds['email'],
                    "password": auth_creds['password']
                },
                headers={"Content-Type": "application/json"},
                timeout=aiohttp.ClientTimeout(total=10)
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    token = data.get('access_token') or data.get('token') or data.get('accessToken')
                    
                    if token:
                        print(f"  OK Token obtenido exitosamente")
                        print(f"  Token (primeros 20 caracteres): {token[:20]}...")
                        return True
                    else:
                        print(f"  ERROR: Token no encontrado en respuesta")
                        print(f"  Respuesta: {data}")
                        return False
                else:
                    error_text = await response.text()
                    print(f"  ERROR: Fallo de autenticación (status {response.status})")
                    print(f"  Respuesta: {error_text}")
                    return False
    
    except aiohttp.ClientConnectorError:
        print(f"  ERROR: No se puede conectar al servidor")
        print(f"  Verifica que el servidor esté corriendo en {config['base_url']}")
        return False
    except Exception as e:
        print(f"  ERROR: Excepción durante autenticación: {e}")
        return False

def main():
    print("="*60)
    print("Validación del Sistema de Pruebas de Rendimiento")
    print("="*60)
    print()
    
    # Validar configuración
    config_valid = validate_config()
    if not config_valid:
        sys.exit(1)
    
    # Validar dependencias
    deps_valid = validate_dependencies()
    if not deps_valid:
        sys.exit(1)
    
    # Validar autenticación
    print("\nNOTA: La validación de autenticación requiere que el servidor esté corriendo.")
    user_input = input("¿Deseas validar la autenticación ahora? (s/n): ")
    
    if user_input.lower() == 's':
        # Cargar configuración
        with open("config.json", 'r') as f:
            config = json.load(f)
        
        auth_valid = asyncio.run(validate_authentication(config))
        if not auth_valid:
            print("\nWARNING: La autenticación falló. Las pruebas pueden no funcionar correctamente.")
            print("Verifica las credenciales y que el servidor esté corriendo.")
    
    print("\n" + "="*60)
    print("Validación completada")
    print("="*60)
    print("\nPuedes ejecutar las pruebas con: ./run_tests.sh")
    print()

if __name__ == "__main__":
    main()
