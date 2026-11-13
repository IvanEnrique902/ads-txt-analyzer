<div align="center">

# 🔍 ads.txt Analyzer

**Analizá cualquier `ads.txt` o `app-ads.txt` en segundos.**
DIRECT vs RESELLER, duplicados, conflictos, plataformas y verificación contra `sellers.json`.

![Python](https://img.shields.io/badge/Python-3.8%2B-3776AB?logo=python&logoColor=white)
![GUI](https://img.shields.io/badge/GUI-tkinter-2e8b57)
![Dependencias](https://img.shields.io/badge/dependencias-0-brightgreen)
![Plataformas](https://img.shields.io/badge/Windows%20%7C%20macOS%20%7C%20Linux-lightgrey)
![AdOps](https://img.shields.io/badge/AdOps-Program%C3%A1tica-3b6fb6)

</div>

---

## 📸 Vista previa

> 👉 Sacale una captura a la ventana con resultados, guardala como `captura.png` en esta carpeta y descomentá la línea de abajo.

<!-- ![Captura de la aplicación](captura.png) -->

---

## 🤔 ¿Qué problema resuelve?

En programática, el archivo **ads.txt** (y **app-ads.txt** para apps) es la lista pública de quién está autorizado a vender el inventario de un sitio. Cada línea tiene este formato:

```
google.com, pub-1111111111111111, DIRECT, f08c47fec0942fa0
```

| Campo | Qué es |
|---|---|
| `google.com` | Plataforma (SSP / exchange) autorizada |
| `pub-1111111111111111` | ID de la cuenta del publisher en esa plataforma |
| `DIRECT` / `RESELLER` | El publisher es dueño de la cuenta (DIRECT) o la cuenta es de un intermediario (RESELLER) |
| `f08c47fec0942fa0` | TAG-ID de la plataforma (opcional) |

Estos archivos pueden tener cientos de líneas, con duplicados, errores y contradicciones. Revisarlos a mano es lento. Esta herramienta lo hace automáticamente y te dice qué mirar.

---

## ✨ Qué hace

| | Función |
|---|---|
| 📊 | Cuenta líneas **DIRECT** vs **RESELLER** con porcentajes y gráfico |
| 🧹 | Detecta **duplicados** exactos |
| ⚠️ | Detecta **conflictos**: el mismo ID declarado como DIRECT y RESELLER a la vez |
| 🏢 | **Agrupa por plataforma** con totales, DIRECT, RESELLER e IDs únicos |
| 🚫 | Marca **líneas inválidas** y lee variables (`OWNERDOMAIN`, `MANAGERDOMAIN`, `CONTACT`, etc.) |
| 💡 | Genera **observaciones automáticas** de buenas prácticas |
| ✅ | **Verifica contra `sellers.json`**: ¿el ID existe? ¿el tipo coincide con DIRECT/RESELLER? |
| 💾 | Exporta a **CSV** y a **reporte TXT** |
| 🖥️ | Funciona con **interfaz gráfica** o desde la **terminal** |

---

## 🚀 Empezar en 30 segundos

**Requisitos:** Python 3.8+. No hay que instalar nada más, usa solo la librería estándar.

```bash
git clone https://github.com/IvanEnrique902/ads-txt-analyzer.git
cd ads-txt-analyzer
python ads_txt_analyzer_completo.py
```

Se abre la ventana y te pide el archivo. Elegí tu `ads.txt` y listo.

> 🐧 En Linux, si falta tkinter: `sudo apt install python3-tk`

---

## 🧰 Modos de uso

### 🖥️ Interfaz gráfica

```bash
python ads_txt_analyzer_completo.py
```

Pestañas: **Resumen** · **Plataformas** · **Registros** · **Duplicados / Errores** · **Verificación sellers.json**

### ⌨️ Terminal

```bash
# Modo interactivo: te pide la ruta (podés arrastrar el archivo a la terminal)
python ads_txt_analyzer_completo.py --terminal

# Analizar un archivo directo
python ads_txt_analyzer_completo.py ejemplos/ads_ejemplo.txt

# Descargar de un dominio y guardar reportes
python ads_txt_analyzer_completo.py --domain sitio.com --report reporte.txt --csv plataformas.csv

# Si es una app
python ads_txt_analyzer_completo.py --domain miapp.com --app

# Verificar una plataforma contra su sellers.json
python ads_txt_analyzer_completo.py --file ejemplos/ads_ejemplo.txt --verify google.com
```

> Si tkinter no está instalado, el programa pasa solo al modo terminal.

---

## 🧪 Ejemplo de salida

Corriendo el archivo de ejemplo incluido:

```
Fuente: ejemplos/ads_ejemplo.txt
Líneas totales en el archivo : 42
Registros válidos            : 14
  DIRECT                     : 7 (50.0%)
  RESELLER                   : 7 (50.0%)
Plataformas distintas        : 8
Duplicados exactos           : 1
Conflictos DIRECT/RESELLER   : 1
Líneas inválidas             : 3

OBSERVACIONES
  - Hay 1 línea(s) duplicada(s): conviene limpiarlas.
  - Hay 1 ID(s) declarados a la vez como DIRECT y RESELLER: revisar.
  - Hay 3 línea(s) inválida(s) que los crawlers van a ignorar.
  - 1 línea(s) sin TAG-ID (campo opcional, pero ayuda a identificar la plataforma).

PLATAFORMAS (total / DIRECT / RESELLER / IDs únicos)
  google.com                        3 /    2 /    1 /    2
  appnexus.com                      2 /    1 /    1 /    1
  openx.com                         2 /    1 /    1 /    2
  ...
```

El archivo `ejemplos/ads_ejemplo.txt` es ficticio y trae errores puestos a propósito (un duplicado, un conflicto y tres líneas inválidas) para que veas cómo los detecta. Los IDs de publisher son inventados.

---

## 🔬 Cómo funciona

```
 ads.txt ──► Parser ──► Análisis ──► Reportes
              │            │            ├─ Interfaz (tkinter)
              │            │            ├─ Terminal
              │            │            └─ CSV / TXT
              │            └─► Verificación con sellers.json (opcional)
              └─ ignora comentarios (#), lee variables y valida cada línea
```

- La **lógica** (parser, análisis, verificación) está separada de la **interfaz**, así se puede reutilizar desde otra interfaz, por ejemplo una web.
- Una línea es válida si tiene al menos 3 campos y la relación es `DIRECT` o `RESELLER`.
- Un **conflicto** es el mismo par plataforma + ID declarado con ambas relaciones.
- La verificación espera que `DIRECT` corresponda a `seller_type` = `PUBLISHER` o `BOTH`, y `RESELLER` a `INTERMEDIARY` o `BOTH`.

---

## 📁 Estructura del proyecto

```
ads-txt-analyzer/
├── ads_txt_analyzer_completo.py   # el programa (lógica + GUI + terminal)
├── ejemplos/
│   └── ads_ejemplo.txt            # archivo de prueba con errores a propósito
├── README.md
└── .gitignore
```

---

<details>
<summary><b>📦 Crear un ejecutable (.exe) para compartirlo</b></summary>

<br>

```bash
pip install pyinstaller
pyinstaller --onefile --windowed --name "ADS Analyzer" ads_txt_analyzer_completo.py
```

El resultado queda en `dist/`. Con ese `.exe`, quien lo reciba no necesita tener Python instalado.

- Compilá en el mismo sistema operativo donde se va a usar.
- Para ponerle ícono: agregá `--icon logo.ico`.
- Algunos antivirus marcan por error los `.exe` hechos con PyInstaller.

</details>

<details>
<summary><b>⚠️ Limitaciones conocidas</b></summary>

<br>

- Algunos `sellers.json` pesan decenas de MB, por lo que la verificación puede tardar.
- Algunos sitios bloquean descargas automáticas. En ese caso, bajá el archivo a mano y abrilo desde el programa.
- La verificación se hace de a una plataforma por vez.

</details>

---

## 🗺️ Próximos pasos

- [ ] Verificar todas las plataformas de una sola vez
- [ ] Comparar el ads.txt de dos dominios
- [ ] Analizar varios dominios en lote
- [ ] Versión web (Streamlit)

---

## 👤 Autor

**Ivan Enrique** · AdOps / Programática
[LinkedIn](https://www.linkedin.com/in/ivanenrique03/)

<div align="center">

⭐ Si te sirvió, dejá una estrella en el repo.

</div>
