# 🌳 LAS LOMAS · Sistema de Control del Proyecto

¡Bienvenido al espacio de trabajo de **LAS LOMAS**! Este es un entorno local unificado y ordenado diseñado para gestionar el desarrollo inmobiliario desde la fase conceptual hasta su entrega. 

Este sistema integra la **página web pública** (para captar inversionistas y compradores) con un **panel de administración interno (CRM, Gantt de Proyecto y Control Financiero)** en una base de datos SQLite local ligera.

---

## 📂 Estructura del Proyecto

El espacio de trabajo se ha organizado de la siguiente manera:
```text
Las Lomas/
│
├── run.py                 # Script de un solo clic para iniciar el servidor y abrir el navegador
├── main.py                # Servidor FastAPI + Controlador de Base de Datos SQLite (lomas.db)
├── requirements.txt       # Dependencias ligeras de Python
├── README.md              # Este manual de usuario
│
├── website/               # Carpeta del sitio web público (Landing Page)
│   ├── index.html         # Página de aterrizaje con formulario conectado al CRM
│   ├── styles.css         # Estilos con paleta de colores natural (verdes, cremas, dorados)
│   └── images/            # Fotografías aéreas, planos de Lomas y recursos gráficos
│
└── admin/                 # Panel de Control Interno
    └── templates/         # Vistas HTML renderizadas dinámicamente
        ├── base.html      # Plantilla base y barra de navegación
        ├── dashboard.html # Panel principal con indicadores clave de rendimiento (KPIs)
        ├── crm.html       # Control de prospectos con alertas de "Sin Responder"
        ├── gantt.html     # Cronograma del proyecto con gráfico Gantt interactivo
        └── finance.html   # Control de caja en Dólares (USD) y Colones (CRC)
```

---

## 🚀 Cómo Iniciar el Sistema (¡Un solo paso!)

Asegúrate de estar en esta carpeta en tu terminal y ejecuta:

```bash
python3 run.py
```

### ¿Qué hace este script automáticamente?
1. **Instala dependencias:** Si no tienes instalados `fastapi`, `uvicorn` o `jinja2`, el script ejecutará `pip install` por ti.
2. **Inicializa la Base de Datos:** Si es la primera vez que se ejecuta, creará el archivo `lomas.db` y **pre-cargará las 16 tareas del cronograma del proyecto** ya espaciadas cronológicamente para que no empieces de cero.
3. **Inicia el Servidor Local:** Levanta el servidor en `http://127.0.0.1:8000`.
4. **Abre el Navegador:** Abre automáticamente tu navegador predeterminado directo en el panel de administración (`http://127.0.0.1:8000/admin`).

---

## 💡 Características Clave Implementadas

### 1. CRM Inmobiliario (Prospectos)
* **Alertas de Seguimiento:** Al igual que en RISE, tienes el control de tus prospectos. Ahora se destaca claramente de color rojo parpadeante si un lead está **"Pendiente de respuesta"** (replied = 0), permitiéndote marcarlo como **"Contestado"** con un solo clic.
* **Captación Directa:** El formulario de descarga del brochure en la landing page está enlazado directamente. Cada vez que alguien ponga su correo en el sitio web público, el prospecto aparecerá automáticamente en tu CRM de administración local al instante.

### 2. Cronograma de Proyecto (Gantt Interactivo)
* **Visualización Dinámica:** Implementado con **Mermaid.js**. El gráfico Gantt se genera automáticamente en tiempo real leyendo tu base de datos de tareas.
* **Control de Tareas y Dependencias:** Puedes crear, editar o eliminar tareas, actualizar el progreso de 0 a 100% y definir si una tarea depende de otra (por ejemplo, *Inscripción de Catastro* t4 ocurre después de *Diseño de Masterplan* t3).
* **Camino Crítico pre-cargado:** Se cargaron las 16 fases clave del desarrollo (SETENA D1, Concesiones de Pozos, Planos, Municipalidad, Pre-ventas, Caminos, Electricidad, Handover) para darte una guía de por dónde empezar.

### 3. Registro Financiero Bimoneda (USD y CRC)
* **Tipo de Cambio en Tiempo Real:** El sistema realiza una consulta automática al iniciar a la API oficial de tipos de cambio de USD a Colones (CRC) y la almacena en caché.
* **Ingresos y Egresos:** Te permite ingresar movimientos en cualquiera de las dos monedas. Si registras un gasto en Colones, calcula automáticamente su equivalente en Dólares en tu balance (y viceversa) basado en el tipo de cambio del día, mostrándote siempre el neto total en ambas monedas.
