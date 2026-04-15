import requests
import time
import os
import sys

def monitor_stats():
    url = "http://localhost:8000/stats"
    print(f"Monitoring translation stats at {url}...")
    print("Press Ctrl+C to stop.\n")
    
    try:
        while True:
            try:
                response = requests.get(url, timeout=2)
                if response.status_code == 200:
                    data = response.json()
                    
                    # Clear screen (optional, depends on preference)
                    # os.system('clear' if os.name == 'posix' else 'cls')
                    
                    print(f"--- {time.strftime('%H:%M:%S')} ---")
                    print(f"Processed: {data.get('processed', 0)} | Current Lag: {data.get('current_lag', 0.0):.2f}s")
                    print(f"Queue Size: {data.get('queue_size', 0)} | Dropped: {data.get('dropped', 0)}")
                    
                    api_keys_data = data.get('api_keys', {})
                    print(f"Total RPM: {api_keys_data.get('total_rpm', 0)}")
                    
                    for key in api_keys_data.get('keys', []):
                        health_str = "HEALTHY" if key['healthy'] else "DISABLED"
                        print(f"  Key: {key['key_id']} ({key['tier']}) | RPM: {key['current_rpm']}/{key['rpm_limit']} | Status: {health_str}")
                    
                    print("-" * 30)
                else:
                    print(f"Error: Received status code {response.status_code}")
            except requests.exceptions.ConnectionError:
                print("Error: Could not connect to the translation service at http://localhost:8000")
            
            time.sleep(2)
    except KeyboardInterrupt:
        print("\nMonitoring stopped.")

if __name__ == "__main__":
    monitor_stats()
