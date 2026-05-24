"""Fix mining_v4.py - fix index bounds in row extraction loop"""
with open(r'E:\quant\factor_research\mining_v4.py', encoding='utf-8') as f:
    content = f.read()

# Fix 1: add early continue if n==0
old1 = 'n = len(feats.get("rsi_14", []))\n'
new1 = 'n = len(feats.get("rsi_14", []))\n            if n == 0:\n                continue\n'
if old1 in content:
    content = content.replace(old1, new1)
    print('Fixed n==0 check')
else:
    print('old1 not found')

# Fix 2: change loop range from n to n-1
old2 = 'for j in range(30, n):\n                row = {\'symbol\': sym,'
new2 = 'for j in range(30, n - 1):\n                row = {\'symbol\': sym,'
if old2 in content:
    content = content.replace(old2, new2)
    print('Fixed loop range: range(30, n-1)')
else:
    print('old2 not found')
    # Try to find it
    idx = content.find("for j in range(30, n):")
    if idx >= 0:
        print(f'  Found at idx {idx}: {repr(content[idx:idx+60])}')

with open(r'E:\quant\factor_research\mining_v4.py', 'w', encoding='utf-8') as f:
    f.write(content)

print('Done')
