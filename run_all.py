import subprocess
import sys
import time
import os

def main():
    print("--- Starting Pipeline ---")
    
    # Define the exact paths based on your folder structure
    mock_endpoint_path = os.path.join("src", "tests", "test_mock_endpoint.py")
    test_script_path = os.path.join("src", "tests", "test_gateway_http.py")
    
    # 1. Start the mock endpoint in the background
    print(f"Booting Mock Endpoint ({mock_endpoint_path})...")
    endpoint_process = subprocess.Popen([sys.executable, mock_endpoint_path])
    
    # 2. Start the gateway in the background
    print("Booting API Gateway (Port 8000)...")
    gateway_process = subprocess.Popen([sys.executable, "-m", "src.gateway.gateway_app"])
    
    # Give the Flask servers 3 seconds to fully start up and bind to their ports
    time.sleep(3)
    print("\n--- Running Tests ---\n")
    
    # 3. Run the test script and wait for it to finish
    try:
        subprocess.run([sys.executable, test_script_path], check=True)
    except subprocess.CalledProcessError:
        print("Tests failed or encountered an error.")
        
    # 4. Clean up background servers
    print("\n--- Shutting Down ---")
    endpoint_process.terminate()
    gateway_process.terminate()
    print("Pipeline closed safely.")

if __name__ == "__main__":
    main()