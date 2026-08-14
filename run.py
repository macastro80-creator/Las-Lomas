import sys
import subprocess
import webbrowser
import time
from threading import Timer

def install_dependencies():
    print("Checking dependencies...")
    try:
        import fastapi
        import uvicorn
        import jinja2
        print("All dependencies are already installed.")
    except ImportError:
        print("Installing dependencies from requirements.txt...")
        try:
            subprocess.check_call([sys.executable, "-m", "pip", "install", "-r", "requirements.txt"])
            print("Dependencies installed successfully.")
        except Exception as e:
            print(f"Error installing dependencies: {e}")
            sys.exit(1)

def open_browser():
    print("Opening web browser...")
    webbrowser.open("http://localhost:8000/admin")

if __name__ == "__main__":
    install_dependencies()
    
    # Open the browser 1.5 seconds after the server starts
    Timer(1.5, open_browser).start()
    
    print("Starting Las Lomas Local Management Server...")
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
