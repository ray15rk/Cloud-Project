import subprocess
import sys
import time

def main():
    print("--- Starting Relay Pipeline ---")

    # 1. Start the gateway in the background
    print("Booting API Gateway (Port 8000)...")
    gateway_process = subprocess.Popen([sys.executable, "-m", "src.gateway.gateway_app"])

    # Give Flask 3 seconds to fully start up and bind
    time.sleep(3)
    print("\n--- Running Tests ---\n")

    # 2. Run the full test suite
    try:
        subprocess.run([sys.executable, "-m", "pytest", "src/tests", "-p", "no:cacheprovider", "-q"], check=True)
    except subprocess.CalledProcessError:
        print("Tests failed or encountered an error.")

    # 3. Clean up
    print("\n--- Shutting Down ---")
    gateway_process.terminate()
    print("Pipeline closed safely.")

if __name__ == "__main__":
    main()