import sys
import subprocess
import json
import struct

# Test native messaging - simulate Chrome sending a message to extension-host.exe
native_host_path = r"C:\Users\RoyGoode\.codex\plugins\cache\openai-bundled\chrome\latest\extension-host\windows\x64\extension-host.exe"

# Test message
message = json.dumps({"type": "ping", "payload": {}}).encode('utf-8')

# Format: 4-byte message length (little-endian) + message
length = struct.pack('<I', len(message))

try:
    proc = subprocess.Popen(
        [native_host_path],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )
    
    # Send message
    proc.stdin.write(length)
    proc.stdin.write(message)
    proc.stdin.flush()
    proc.stdin.close()
    
    # Read response (first 4 bytes = length)
    response_length_data = proc.stdout.read(4)
    if response_length_data and len(response_length_data) >= 4:
        response_length = struct.unpack('<I', response_length_data)[0]
        response_data = proc.stdout.read(response_length)
        print(f"Response ({response_length} bytes):")
        try:
            response = json.loads(response_data.decode('utf-8'))
            print(json.dumps(response, indent=2, ensure_ascii=False))
        except:
            print(response_data.decode('utf-8', errors='replace'))
    else:
        print("No response received")
    
    # Check stderr
    stderr = proc.stderr.read()
    if stderr:
        print(f"STDERR: {stderr.decode('utf-8', errors='replace')}")
    
    proc.wait(timeout=10)
    print(f"\nExit code: {proc.returncode}")
    
except Exception as e:
    print(f"ERROR: {e}")