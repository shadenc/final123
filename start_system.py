#!/usr/bin/env python3
"""
Financial Analysis System - ONE CLICK START
Just run this file and everything will work!
"""

import os
import sys
import subprocess
import platform
import time
import webbrowser
from pathlib import Path

PROMPT_PRESS_ENTER_EXIT = "Press Enter to exit..."

def run_command(command, shell=False):
    """Run a command and return success status"""
    try:
        result = subprocess.run(command, shell=shell, capture_output=True, text=True)
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False

def check_python():
    """Check if Python is available"""
    print("Checking Python...")
    if run_command([sys.executable, "--version"]):
        print("Python found")
        return True
    else:
        print("Python not found. Please install Python 3.8+ from python.org")
        input(PROMPT_PRESS_ENTER_EXIT)
        return False

def check_node():
    """Check if Node.js is available"""
    print("Checking Node.js...")
    if run_command(["node", "--version"]):
        print("Node.js found")
        return True
    else:
        print("Node.js not found. Please install Node.js 14+ from nodejs.org")
        input(PROMPT_PRESS_ENTER_EXIT)
        return False

def setup_virtual_environment():
    """Create and setup virtual environment"""
    print("\nSetting up virtual environment...")
    
    venv_name = "Foreign Investment"
    if not os.path.exists(venv_name):
        print("Creating virtual environment...")
        if not run_command([sys.executable, "-m", "venv", venv_name]):
            print("Failed to create virtual environment")
            return False
        print("Virtual environment created")
    else:
        print("Virtual environment already exists")
    
    # Activate and install dependencies
    if platform.system() == "Windows":
        pip_path = os.path.join(venv_name, "Scripts", "pip.exe")
        python_path = os.path.join(venv_name, "Scripts", "python.exe")
    else:
        pip_path = os.path.join(venv_name, "bin", "pip")
        python_path = os.path.join(venv_name, "bin", "python")
    
    print("Installing Python packages...")
    if not run_command([pip_path, "install", "-r", "requirements.txt"]):
        print("Python dependencies failed")
        return False
    
    print("Python packages installed")
    return python_path

def install_frontend_dependencies():
    """Install Node.js dependencies"""
    print("Installing Node.js packages...")
    os.chdir("frontend")
    if not run_command(["npm", "install"]):
        print("Node.js dependencies failed")
        return False
    os.chdir("..")
    print("Node.js packages installed")
    return True

def start_services(python_path):
    """Start both API server and frontend"""
    print("\nStarting services...")
    
    # Start API server
    print("Starting API server...")
    api_script = "src/api/evidence_api.py"
    if platform.system() == "Windows":
        api_server = subprocess.Popen([python_path, api_script], 
                                    creationflags=subprocess.CREATE_NEW_CONSOLE)
    else:
        api_server = subprocess.Popen([python_path, api_script])
    
    # Wait for API to start
    time.sleep(5)
    
    # Start frontend
    print("Starting frontend...")
    os.chdir("frontend")
    if platform.system() == "Windows":
        frontend = subprocess.Popen(["npm", "start"], 
                                  creationflags=subprocess.CREATE_NEW_CONSOLE)
    else:
        frontend = subprocess.Popen(["npm", "start"])
    os.chdir("..")
    
    # Wait for frontend to start
    time.sleep(10)
    
    return api_server, frontend

def main():
    """Main function - everything happens here"""
    print("=" * 60)
    print("Financial Analysis System")
    print("=" * 60)
    print("ONE CLICK START - Everything will be set up automatically!")
    print()
    
    # Check prerequisites
    if not check_python() or not check_node():
        return
    
    # Setup virtual environment and install dependencies
    python_path = setup_virtual_environment()
    if not python_path:
        input(PROMPT_PRESS_ENTER_EXIT)
        return
    
    if not install_frontend_dependencies():
        input(PROMPT_PRESS_ENTER_EXIT)
        return
    
    # Start services
    api_server, frontend = start_services(python_path)
    
    # Success!
    print("\n" + "=" * 60)
    print("SUCCESS! Your Financial Analysis System is ready!")
    print("=" * 60)
    print("API Server: http://localhost:5003")
    print("Frontend: http://localhost:3000")
    print()
    
    # Open browser
    try:
        webbrowser.open("http://localhost:3000")
        print("Browser opened automatically!")
    except (webbrowser.Error, OSError):
        print("Please open: http://localhost:3000")
    
    print("\nTo stop: Close the command windows")
    print("To restart: Run this script again")
    print("\nEnjoy your Financial Analysis System!")
    
    # Keep running
    try:
        input("\nPress Enter to stop all services...")
    except KeyboardInterrupt:
        pass
    
    print("\nStopping services...")
    api_server.terminate()
    frontend.terminate()
    print("Done!")

if __name__ == "__main__":
    main()
