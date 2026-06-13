import socket, subprocess, json, sys

# 1. Check if extension-host.exe can start
native_host = r"C:\Users\RoyGoode\.codex\plugins\cache\openai-bundled\chrome\latest\extension-host\windows\x64\extension-host.exe"
print(f"[1] extension-host.exe exists: {__import__('os').path.exists(native_host)}")

# 2. Try common WebSocket ports
print("\n[2] Checking common ports for Codex WebSocket server...")
for port in [9222, 9229, 9230, 3000, 3001, 8080, 8081, 5000, 5001, 5173, 5174, 4180, 4181]:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(0.5)
    result = sock.connect_ex(('127.0.0.1', port))
    if result == 0:
        print(f"  Port {port}: OPEN")
        try:
            data = sock.recv(1024)
            print(f"    Data: {data[:200]}")
        except:
            pass
    sock.close()

# 3. Check if the native messaging host is properly registered
print("\n[3] Checking native messaging registration...")
import os
nmh_path = os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\User Data\NativeMessagingHosts\com.openai.codexextension.json")
print(f"  Registration file exists: {os.path.exists(nmh_path)}")
if os.path.exists(nmh_path):
    with open(nmh_path) as f:
        print(f"  Content: {f.read()}")

# 4. Get Codex debug info from running processes
print("\n[4] Checking for running Codex processes...")
result = subprocess.run(['tasklist', '/FI', 'IMAGENAME eq Codex.exe'], capture_output=True, text=True, timeout=5)
if 'Codex.exe' in result.stdout:
    # Count lines with Codex (minus header)
    lines = [l for l in result.stdout.split('\n') if 'Codex' in l]
    print(f"  Found {len(lines)} Codex.exe processes")
else:
    print("  No Codex.exe processes found")

result2 = subprocess.run(['tasklist', '/FI', 'IMAGENAME eq codex.exe'], capture_output=True, text=True, timeout=5)
lines2 = [l for l in result2.stdout.split('\n') if 'codex' in l.lower() and 'codex' not in l.split()[:1]]
print(f"  codex.exe (lowercase): {len(lines2) if lines2 else 0}")
print("  All processes named Codex/codex:")
for line in result.stdout.split('\n') + result2.stdout.split('\n'):
    if 'codex' in line.lower():
        print(f"    {line.strip()}")

print("\n=== Done ===")